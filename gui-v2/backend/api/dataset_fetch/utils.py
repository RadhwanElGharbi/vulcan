from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException

from .constants import (
    DB_INDEX_CSV,
    DB_LOCK,
    DB_MATERIALIZE_MODE,
    DBS_ROOT,
    GDALINFO_BIN,
    OGR2OGR_BIN,
    OGRINFO_BIN,
    PROTOCOL_PATH,
    ZEUS_BIN,
    _EXTENT_RE,
    _PROTOCOL_TEXT_CACHE,
    _ZEUS_VERSION_CACHE,
)
from .models import FetchContext
from ..project_utils import resolve_project_path


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Protocol / metadata helpers
# ---------------------------------------------------------------------------


def _load_protocol_text() -> str:
    global _PROTOCOL_TEXT_CACHE
    if _PROTOCOL_TEXT_CACHE is not None:
        return _PROTOCOL_TEXT_CACHE
    try:
        _PROTOCOL_TEXT_CACHE = PROTOCOL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        _PROTOCOL_TEXT_CACHE = ""
    return _PROTOCOL_TEXT_CACHE


def _load_project_metadata_blob(ctx: FetchContext) -> str:
    metadata_path = ctx.project_path / "project_metadata.json"
    if metadata_path.exists():
        try:
            return metadata_path.read_text(encoding="utf-8")
        except OSError:
            return "{}"
    return "{}"


def _extract_json_payload(text: str) -> Dict[str, Any]:
    """Extract JSON payload from potentially markdown-wrapped response.

    Enhanced to handle:
    - JSON in ```json blocks
    - JSON in ``` blocks (untyped)
    - Raw JSON in response
    - JSON with leading/trailing text
    - Nested JSON objects
    - Multiple JSON blocks (returns first valid one)
    """
    candidate = text.strip()

    code_block_patterns = [
        r'```json\s*([\s\S]*?)\s*```',
        r'```\s*([\s\S]*?)\s*```',
    ]
    for pattern in code_block_patterns:
        matches = re.findall(pattern, candidate, re.IGNORECASE)
        for match in matches:
            match = match.strip()
            if match.startswith("{") and match.endswith("}"):
                try:
                    return json.loads(match)
                except json.JSONDecodeError:
                    continue

    if candidate.startswith("{"):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    start = candidate.find("{")
    if start != -1:
        depth = 0
        end = -1
        for i, char in enumerate(candidate[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            json_str = candidate[start:end + 1]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = candidate[start:end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not extract valid JSON from response", candidate, 0)


# ---------------------------------------------------------------------------
# Bbox / extent helpers
# ---------------------------------------------------------------------------


def _build_bbox_string(bbox: Tuple[float, float, float, float]) -> str:
    return ",".join(f"{value:.6f}" for value in bbox)


def _buffer_bbox(bbox: Tuple[float, float, float, float], ratio: float = 0.02) -> Tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = bbox
    width = max(max_x - min_x, 0.01)
    height = max(max_y - min_y, 0.01)
    expand_x = width * ratio
    expand_y = height * ratio
    return (
        max(-180.0, min_x - expand_x),
        max(-90.0, min_y - expand_y),
        min(180.0, max_x + expand_x),
        min(90.0, max_y + expand_y),
    )


def _parse_project_bbox(project_path: Path) -> Optional[Tuple[float, float, float, float]]:
    project_aoi = project_path / "aoi" / "project_aoi.json"
    if not project_aoi.exists():
        return None
    try:
        data = json.loads(project_aoi.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    start = data.get("start_point") or {}
    end = data.get("end_point") or {}
    if not all(k in start for k in ("latitude", "longitude")):
        return None
    if not all(k in end for k in ("latitude", "longitude")):
        return None
    min_lon = min(start["longitude"], end["longitude"])
    max_lon = max(start["longitude"], end["longitude"])
    min_lat = min(start["latitude"], end["latitude"])
    max_lat = max(start["latitude"], end["latitude"])
    return (float(min_lon), float(min_lat), float(max_lon), float(max_lat))


def _extent_from_cutline(path: Path) -> Optional[Tuple[float, float, float, float]]:
    if not path.exists():
        return None
    cmd = [
        OGR2OGR_BIN,
        "-f",
        "GeoJSON",
        "-t_srs",
        "EPSG:4326",
        "/vsistdout/",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ogr2ogr extent extraction failed: {result.stderr}")
        return None
    try:
        geojson = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]

    def _walk(coords):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            x, y = coords[:2]
            bounds[0] = min(bounds[0], x)
            bounds[1] = min(bounds[1], y)
            bounds[2] = max(bounds[2], x)
            bounds[3] = max(bounds[3], y)
            return
        for part in coords:
            _walk(part)

    def _collect(geom):
        if not geom:
            return
        if geom.get("type") == "GeometryCollection":
            for g in geom.get("geometries", []):
                _collect(g)
        else:
            _walk(geom.get("coordinates"))

    if geojson.get("type") == "FeatureCollection":
        for feature in geojson.get("features", []):
            _collect(feature.get("geometry"))
    elif geojson.get("type") == "Feature":
        _collect(geojson.get("geometry"))
    else:
        _collect(geojson)

    if (
        float("inf") in bounds
        or float("-inf") in bounds
        or bounds[0] == bounds[2]
        or bounds[1] == bounds[3]
    ):
        return None

    return tuple(bounds)  # type: ignore[arg-type]


def _copernicus_tile_name(lat: int, lon: int) -> str:
    lat_hem = "N" if lat >= 0 else "S"
    lon_hem = "E" if lon >= 0 else "W"
    return f"{lat_hem}{abs(lat):02d}_00_{lon_hem}{abs(lon):03d}_00"


# ---------------------------------------------------------------------------
# Network / hashing helpers
# ---------------------------------------------------------------------------


def _http_get_json(url: str, params: Dict[str, str], timeout: int = 60) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}" if query else url
    with urllib.request.urlopen(full_url, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    return json.loads(payload)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Global DB index helpers
# ---------------------------------------------------------------------------


def _ensure_db_index_exists() -> None:
    DBS_ROOT.mkdir(parents=True, exist_ok=True)
    if DB_INDEX_CSV.exists():
        return
    header = [
        "db_id",
        "dataset_name",
        "provider",
        "provider_url",
        "source",
        "source_url",
        "license",
        "attribution",
        "raw_relpath",
        "sha256",
        "size_bytes",
        "acquired_utc",
    ]
    DB_INDEX_CSV.write_text(",".join(header) + "\n", encoding="utf-8")


def _upsert_db_index_row(row: Dict[str, str]) -> None:
    _ensure_db_index_exists()
    existing_rows: List[Dict[str, str]] = []
    try:
        with DB_INDEX_CSV.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if not r:
                    continue
                existing_rows.append({k: (v or "").strip() for k, v in r.items()})
    except OSError:
        existing_rows = []

    replaced = False
    out_rows: List[Dict[str, str]] = []
    for r in existing_rows:
        if (r.get("db_id") or "").strip() == (row.get("db_id") or "").strip():
            out_rows.append(row)
            replaced = True
        else:
            out_rows.append(r)
    if not replaced:
        out_rows.append(row)

    header = [
        "db_id",
        "dataset_name",
        "provider",
        "provider_url",
        "source",
        "source_url",
        "license",
        "attribution",
        "raw_relpath",
        "sha256",
        "size_bytes",
        "acquired_utc",
    ]
    tmp = DB_INDEX_CSV.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for r in out_rows:
            writer.writerow({k: (r.get(k) or "") for k in header})
    tmp.replace(DB_INDEX_CSV)


def _materialize_db_raw(
    src: Path,
    dst: Path,
    *,
    log: Optional[Callable[[str], None]] = None,
) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dst.exists() or dst.is_symlink():
            dst.unlink()
    except OSError:
        pass

    mode = (DB_MATERIALIZE_MODE or "symlink").strip().lower()
    attempted: List[str] = []
    used: Optional[str] = None

    def _try_symlink() -> bool:
        attempted.append("symlink")
        try:
            dst.symlink_to(src)
            nonlocal used
            used = "symlink"
            return True
        except OSError:
            return False

    def _try_hardlink() -> bool:
        attempted.append("hardlink")
        try:
            os.link(src, dst)
            nonlocal used
            used = "hardlink"
            return True
        except OSError:
            return False

    def _try_copy() -> bool:
        attempted.append("copy")
        try:
            shutil.copy2(src, dst)
            nonlocal used
            used = "copy"
            return True
        except OSError:
            return False

    succeeded = False
    if mode == "hardlink":
        succeeded = _try_hardlink() or _try_symlink() or _try_copy()
    elif mode == "copy":
        succeeded = _try_copy() or _try_symlink() or _try_hardlink()
    else:  # default: symlink
        succeeded = _try_symlink() or _try_hardlink() or _try_copy()

    if not succeeded:
        raise RuntimeError(f"Failed to materialize DB raw file into project: tried {attempted}")
    if not used:
        # Defensive fallback: infer by file type / inode
        try:
            if dst.is_symlink():
                used = "symlink"
            else:
                try:
                    if dst.stat().st_ino == src.stat().st_ino:
                        used = "hardlink"
                    else:
                        used = "copy"
                except OSError:
                    used = "copy"
        except OSError:
            used = "copy"

    if log:
        log(f"DB cache materialized raw file using mode='{used}': {dst} -> {src}")
    return used


# ---------------------------------------------------------------------------
# Project context helpers
# ---------------------------------------------------------------------------


def _resolve_aoi_file(project_path: Path) -> Tuple[Path, Path]:
    candidates = [
        project_path / "data" / "vectors" / "aoi.gpkg",
        project_path / "aoi" / "aoi.gpkg",
        project_path / "aoi" / "aoi.geojson",
        project_path / "aoi" / "aoi.kmz",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate, candidate
    raise HTTPException(status_code=400, detail="AOI file not found (expected data/vectors/aoi.gpkg or aoi/aoi.kmz)")


def _ensure_command_available(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"ZEUS binary not found at {path}")
    if not os.access(path, os.X_OK):
        raise HTTPException(status_code=400, detail=f"ZEUS binary at {path} is not executable")


def _lazy_infer_iso3_list(project_path: Path) -> List[str]:
    """Detect ALL countries the AOI intersects, returning ISO3 codes.

    Uses _aoi_countries_admin0 (local Natural Earth intersection, no network)
    for reliable multi-country detection.  Falls back to the single-country
    _infer_project_iso3 when boundaries aren't cached.
    """
    try:
        from ..projects import (  # type: ignore
            _load_project_aoi_feature_collection,
            _aoi_countries_admin0,
            _collect_geometry,
            NATURAL_EARTH_ADMIN0_SHP,
        )
        if NATURAL_EARTH_ADMIN0_SHP.exists():
            aoi_fc = _load_project_aoi_feature_collection(project_path)
            if isinstance(aoi_fc, dict):
                geom = _collect_geometry(aoi_fc)
                countries = _aoi_countries_admin0(geom)
                if countries:
                    return sorted({str(c).strip().upper() for c in countries if c})
    except Exception:  # noqa: BLE001
        pass

    # Keep all AOI countries recorded at creation if boundary data is unavailable.
    try:
        metadata = json.loads((project_path / 'project_metadata.json').read_text(encoding='utf-8'))
        countries = metadata.get('iso3_list') or []
        if isinstance(countries, list) and countries:
            import pycountry
            codes = sorted({str(code).strip().upper() for code in countries if pycountry.countries.get(alpha_3=str(code).strip().upper())})
            if codes:
                return codes
    except (OSError, ValueError):
        pass

    try:
        from ..projects import _infer_project_iso3  # type: ignore
        single = _infer_project_iso3(project_path)
        if single:
            return [single]
    except ImportError:
        pass
    return []


def _load_project_context(project: str) -> FetchContext:
    project_path = resolve_project_path(project)
    if not project_path or not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    metadata_path = project_path / "project_metadata.json"
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metadata = {}

    # Support both nested crs object (new standard) and flat crs_epsg (legacy)
    crs_obj = metadata.get("crs")
    if isinstance(crs_obj, dict):
        target_epsg = crs_obj.get("epsg")
        target_crs_name = crs_obj.get("name")
    else:
        target_epsg = metadata.get("crs_epsg")
        target_crs_name = metadata.get("crs_name")
    
    if not isinstance(target_epsg, int):
        raise HTTPException(status_code=400, detail="project_metadata.json must define CRS (either 'crs.epsg' or legacy 'crs_epsg')")

    aoi_file, cutline_path = _resolve_aoi_file(project_path)
    bbox = _extent_from_cutline(cutline_path) or _parse_project_bbox(project_path)
    if not bbox:
        raise HTTPException(status_code=400, detail="Unable to infer AOI bounding box. Ensure project_aoi.json exists.")

    buffered_bbox = _buffer_bbox(bbox)

    logs_dir = project_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "dataset_fetch.log"

    ctx = FetchContext(
        project=project,
        project_path=project_path,
        target_epsg=target_epsg,
        target_crs_name=target_crs_name,
        iso3_list=_lazy_infer_iso3_list(project_path),
        bbox=buffered_bbox,
        bbox_string=_build_bbox_string(buffered_bbox),
        aoi_file=aoi_file,
        cutline_path=cutline_path,
        log_dir=logs_dir,
        log_file=log_file,
        protocol_version=metadata.get("dataset_protocol_version", "1.0"),
    )

    from .constants import PROTOCOL_VERSION
    if ctx.protocol_version != PROTOCOL_VERSION:
        print(f"[DatasetFetch] Project '{project}' uses protocol v{ctx.protocol_version} (current: v{PROTOCOL_VERSION})")

    return ctx


# ---------------------------------------------------------------------------
# GDAL / OGR helpers
# ---------------------------------------------------------------------------

_GDALINFO_STATS_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB


def _gdal_info(path: Path) -> Optional[Dict]:
    """
    Lightweight GDAL info helper.

    NOTE: `gdalinfo -stats` can be extremely slow for very large rasters (multi-GB DEM/landcover),
    and can make fetch jobs look "stuck" during validation/metadata. We only request stats for
    smaller rasters; for large rasters we prefer fast structural metadata (CRS/extent).
    """
    include_stats = False
    try:
        include_stats = path.stat().st_size <= _GDALINFO_STATS_MAX_BYTES
    except OSError:
        include_stats = False

    cmd = [GDALINFO_BIN, "-json"]
    if include_stats:
        cmd.append("-stats")
    cmd.append(str(path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _ogr_info(path: Path) -> Optional[Dict]:
    # IMPORTANT: Do NOT use -q with -json here.
    # `ogrinfo -q -json` omits geometryFields (extent/CRS) which we need for protocol-compliant metadata.
    result = subprocess.run([OGRINFO_BIN, "-al", "-so", "-json", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Raster / vector inspection helpers
# ---------------------------------------------------------------------------


def _extract_epsg_from_info(info: Dict[str, Any]) -> Optional[str]:
    cs = info.get("coordinateSystem") or {}
    wkt = cs.get("wkt")
    if not wkt:
        return None
    # Prefer the LAST EPSG code in the WKT. Many WKT2 strings include a base CRS
    # (e.g., EPSG:4326) before the projected CRS ID (e.g., EPSG:32613).
    ids = re.findall(r'ID\["EPSG",\s*(\d+)\]', wkt)
    if not ids:
        ids = re.findall(r'AUTHORITY\["EPSG","(\d+)"\]', wkt)
    if ids:
        return f"EPSG:{ids[-1]}"
    return None


def _collect_bounds_from_geojson(geom: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]

    def _walk(coords: Any) -> None:
        if coords is None:
            return
        if isinstance(coords, (list, tuple)):
            if coords and isinstance(coords[0], (int, float)):
                x, y = coords[:2]
                bounds[0] = min(bounds[0], float(x))
                bounds[1] = min(bounds[1], float(y))
                bounds[2] = max(bounds[2], float(x))
                bounds[3] = max(bounds[3], float(y))
            else:
                for part in coords:
                    _walk(part)

    def _collect(obj: Any) -> None:
        if obj is None:
            return
        geom_type = obj.get("type")
        if geom_type == "FeatureCollection":
            for feature in obj.get("features", []):
                _collect(feature.get("geometry"))
        elif geom_type == "Feature":
            _collect(obj.get("geometry"))
        elif geom_type == "GeometryCollection":
            for g in obj.get("geometries", []):
                _collect(g)
        else:
            _walk(obj.get("coordinates"))

    _collect(geom)
    if (
        float("inf") in bounds
        or float("-inf") in bounds
        or bounds[0] == bounds[2]
        or bounds[1] == bounds[3]
    ):
        return None
    return bounds[0], bounds[1], bounds[2], bounds[3]


def _extent_from_gdal_info(info: Dict[str, Any]) -> Optional[Dict[str, float]]:
    corners = info.get("cornerCoordinates")
    if not isinstance(corners, dict):
        return None
    xs = []
    ys = []
    for point in corners.values():
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    if not xs or not ys:
        return None
    return {
        "minx": min(xs),
        "miny": min(ys),
        "maxx": max(xs),
        "maxy": max(ys),
        "crs": "EPSG:4326",
    }


def _bbox_from_wgs84_extent(extent_geom: Dict[str, Any]) -> Optional[Dict[str, float]]:
    bounds = _collect_bounds_from_geojson(extent_geom)
    if not bounds:
        return None
    minx, miny, maxx, maxy = bounds
    return {"west": minx, "south": miny, "east": maxx, "north": maxy, "crs": "EPSG:4326"}


def _bbox_dict_from_tuple(bbox: Tuple[float, float, float, float]) -> Dict[str, float]:
    minx, miny, maxx, maxy = bbox
    return {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy, "crs": "EPSG:4326"}


def _bbox_wgs84_from_tuple(bbox: Tuple[float, float, float, float]) -> Dict[str, float]:
    minx, miny, maxx, maxy = bbox
    return {"west": minx, "south": miny, "east": maxx, "north": maxy, "crs": "EPSG:4326"}


def _compute_file_sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _extract_raster_statistics(info: Dict[str, Any]) -> Optional[Dict[str, float]]:
    bands = info.get("bands")
    if not isinstance(bands, list) or not bands:
        return None
    band = bands[0]
    stats_meta = band.get("metadata", {}).get("", {})
    if not stats_meta:
        return None

    def _safe_float(value: Optional[str]) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    stats = {
        "min": _safe_float(stats_meta.get("STATISTICS_MINIMUM")),
        "max": _safe_float(stats_meta.get("STATISTICS_MAXIMUM")),
        "mean": _safe_float(stats_meta.get("STATISTICS_MEAN")),
        "stddev": _safe_float(stats_meta.get("STATISTICS_STDDEV")),
        "valid_percent": _safe_float(stats_meta.get("STATISTICS_VALID_PERCENT")),
    }
    return {k: v for k, v in stats.items() if v is not None}


def _status_from_issues(errors: List[str], warnings: List[str]) -> str:
    if errors:
        return "failed"
    if warnings:
        return "passed_with_warnings"
    return "passed"


def _bbox_wgs84_covers_target(bbox_wgs84: Dict[str, float], target: Tuple[float, float, float, float], tol: float = 0.01) -> bool:
    west, south, east, north = target
    try:
        return (
            float(bbox_wgs84["west"]) <= float(west) + tol
            and float(bbox_wgs84["south"]) <= float(south) + tol
            and float(bbox_wgs84["east"]) >= float(east) - tol
            and float(bbox_wgs84["north"]) >= float(north) - tol
        )
    except Exception:  # noqa: BLE001
        return False


def _bbox_wgs84_within_target(bbox_wgs84: Dict[str, float], target: Tuple[float, float, float, float], tol: float = 1e-6) -> bool:
    west, south, east, north = target
    try:
        return (
            float(bbox_wgs84["west"]) >= float(west) - tol
            and float(bbox_wgs84["south"]) >= float(south) - tol
            and float(bbox_wgs84["east"]) <= float(east) + tol
            and float(bbox_wgs84["north"]) <= float(north) + tol
        )
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Vector inspection helpers
# ---------------------------------------------------------------------------


def _vector_feature_count(info: Dict[str, Any]) -> Optional[int]:
    layers = info.get("layers")
    if not isinstance(layers, list) or not layers:
        return None
    layer = layers[0]
    count = layer.get("featureCount")
    try:
        return int(count) if count is not None else None
    except (TypeError, ValueError):
        return None


def _vector_epsg(path: Path) -> Optional[str]:
    info = _ogr_info(path)
    if not info:
        return None
    layers = info.get("layers") or []
    if not layers:
        return None
    layer = layers[0]
    gfields = layer.get("geometryFields") or []
    if not gfields:
        return None
    cs = (gfields[0].get("coordinateSystem") or {}) if isinstance(gfields[0], dict) else {}
    wkt = cs.get("wkt")
    if not isinstance(wkt, str) or not wkt.strip():
        return None
    # Support both modern WKT2 `ID["EPSG",32613]` and legacy WKT1 `AUTHORITY["EPSG","32613"]`.
    # Prefer the LAST EPSG code in the WKT (e.g., projected CRS often contains a base EPSG:4326 first).
    ids = re.findall(r'ID\["EPSG",\s*(\d+)\]', wkt)
    if not ids:
        ids = re.findall(r'AUTHORITY\["EPSG","(\d+)"\]', wkt)
    if ids:
        return f"EPSG:{ids[-1]}"
    return None


def _vector_bbox_wgs84(path: Path) -> Optional[Dict[str, float]]:
    extent = _vector_extent_dict(path)
    if not extent:
        return None
    crs = str(extent.get("crs") or "")
    minx = float(extent["minx"])
    miny = float(extent["miny"])
    maxx = float(extent["maxx"])
    maxy = float(extent["maxy"])

    if crs.upper() == "EPSG:4326":
        return {"west": minx, "south": miny, "east": maxx, "north": maxy, "crs": "EPSG:4326"}

    if not crs.upper().startswith("EPSG:"):
        return None

    try:
        from pyproj import Transformer
    except Exception:  # noqa: BLE001
        return None

    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    xs: List[float] = []
    ys: List[float] = []
    for x, y in ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)):
        try:
            lon, lat = transformer.transform(x, y)
            xs.append(float(lon))
            ys.append(float(lat))
        except Exception:  # noqa: BLE001
            continue
    if not xs or not ys:
        return None
    return {"west": min(xs), "south": min(ys), "east": max(xs), "north": max(ys), "crs": "EPSG:4326"}


def _vector_extent_dict(path: Path) -> Optional[Dict[str, object]]:
    """Return layer extent in its native CRS, plus CRS identifier if available."""
    info = _ogr_info(path)
    if not info:
        return None
    layers = info.get("layers") or []
    if not layers:
        return None
    layer = layers[0]
    gfields = layer.get("geometryFields") or []
    if not gfields or not isinstance(gfields[0], dict):
        return None
    extent = gfields[0].get("extent")
    if not (isinstance(extent, list) and len(extent) >= 4):
        return None
    # OGR JSON uses [minx, miny, maxx, maxy]
    minx, miny, maxx, maxy = (float(extent[0]), float(extent[1]), float(extent[2]), float(extent[3]))
    crs = _vector_epsg(path) or "unknown"
    return {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy, "crs": crs}


def _vector_feature_count_ogr(path: Path) -> Optional[int]:
    info = _ogr_info(path)
    if info:
        count = _vector_feature_count(info)
        if count is not None:
            return count
    # Fallback: parse text output
    result = subprocess.run([OGRINFO_BIN, "-al", "-so", "-q", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    match = re.search(r"Feature Count:\\s*(\\d+)", result.stdout)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------


def with_retries(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    retryable_exceptions: tuple = (RuntimeError, OSError, ConnectionError),
    log_fn: Optional[Callable[[str], None]] = None,
):
    """Decorator that retries a function with exponential backoff."""
    import functools
    import time as _time

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retryable_exceptions as exc:
                    if attempt == max_attempts:
                        raise
                    delay = backoff_base ** attempt
                    msg = f"Retry {attempt}/{max_attempts} for {fn.__name__} after {delay:.1f}s: {exc}"
                    if log_fn:
                        log_fn(msg)
                    _time.sleep(delay)
        return wrapper
    return decorator

