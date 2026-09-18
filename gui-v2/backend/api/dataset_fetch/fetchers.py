"""
Dataset fetcher functions.

Each fetcher is responsible for downloading raw data from a specific provider
(Copernicus, OpenTopo/3DEP, OSM Overpass, ArcGIS REST, NHN, etc.) and
returning (command_list, metadata_overrides).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .constants import (
    CAN_INDIGENOUS_LANDS_DB_ID,
    CAN_INDIGENOUS_LANDS_DB_RAW_FILENAME,
    CER_PIPELINES_LAYER_URL,
    CLSS_ABORIGINAL_LANDS_LAYER_URL,
    COPERNICUS_BASE_URL,
    COPERNICUS_PRODUCT,
    CPCAD_LAYER_URL,
    DB_LOCK,
    DBS_ROOT,
    GDALWARP_BIN,
    NHN_INDEX_ZIP_URL,
    NHN_TILE_BASE_URL,
    OGR2OGR_BIN,
    ZEUS_BIN,
)
from .models import FetchContext, OSM_OVERPASS_FILTERS, OVERPASS_ENDPOINTS
from .job_state import DatasetJobState, JOB_LOCK, _run_command, _log_to_job
from .utils import (
    _copernicus_tile_name,
    _ensure_db_index_exists,
    _http_get_json,
    _materialize_db_raw,
    _sha256_file,
    _upsert_db_index_row,
    _utc_iso,
    _utc_now,
    _write_json,
    with_retries,
)


MAX_TILE_CONCURRENCY = int(os.getenv("DATASET_TILE_CONCURRENCY", "4"))


def _download_single_copernicus_tile(
    tile_info: Tuple[str, int, int],
    download_dir: Path,
    job: DatasetJobState,
    ctx: FetchContext,
    total_tiles: int,
    progress_state: Dict[str, int],
    progress_lock: threading.Lock,
) -> Tuple[str, Optional[Path], bool]:
    """Download one Copernicus DEM tile. Returns (tile_name, dest_path | None, was_skipped)."""
    tile, lat, lon = tile_info
    folder = f"{COPERNICUS_PRODUCT}_{tile}_DEM"
    filename = f"{COPERNICUS_PRODUCT}_{tile}_DEM.tif"
    url = f"{COPERNICUS_BASE_URL}/{folder}/{filename}"
    dest = download_dir / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "curl",
        "-sSfL",
        "--connect-timeout",
        "30",
        "--max-time",
        "1200",
        "--retry",
        "3",
        "--retry-delay",
        "2",
        "--retry-connrefused",
        url,
        "-o",
        str(dest),
    ]
    _log_to_job(job, ctx, f"Downloading Copernicus tile {tile} from {url}")
    try:
        _run_command(cmd, ctx.project_path, job, ctx, f"Copernicus {tile}")
    except RuntimeError as exc:
        _log_to_job(
            job,
            ctx,
            f"Copernicus tile {tile} unavailable ({exc}); skipping (will be NoData in mosaic for this tile).",
        )
        _advance_copernicus_tile_progress(job, total_tiles, progress_state, progress_lock)
        return tile, None, True

    if not dest.exists() or dest.stat().st_size < 1024:
        _log_to_job(job, ctx, f"Copernicus tile {tile} download produced empty file; skipping.")
        _advance_copernicus_tile_progress(job, total_tiles, progress_state, progress_lock)
        return tile, None, True

    _advance_copernicus_tile_progress(job, total_tiles, progress_state, progress_lock)
    return tile, dest, False


def _advance_copernicus_tile_progress(
    job: DatasetJobState,
    total_tiles: int,
    progress_state: Dict[str, int],
    progress_lock: threading.Lock,
) -> None:
    """Thread-safe progress update for parallel Copernicus tile downloads."""
    if len(getattr(job, "categories", []) or []) != 1:
        return
    with progress_lock:
        progress_state["done"] += 1
        done = progress_state["done"]
    with JOB_LOCK:
        job.progress = min(0.9, (done / max(total_tiles, 1)) * 0.9)
        job.updated_at = _utc_now()


def _copernicus_fetch(ctx: FetchContext, raw_path: Path, job: DatasetJobState) -> Tuple[List[str], Dict[str, object]]:
    # Resume support: if a previous attempt already produced the raw mosaic in this
    # staging location, reuse it to avoid re-downloading/re-mosaicking.
    try:
        if raw_path.exists() and raw_path.stat().st_size > 1024:
            _log_to_job(job, ctx, f"Copernicus raw mosaic already present; reusing: {raw_path}")
            return ["reuse_existing_raw_mosaic", str(raw_path)], {
                "dem_dataset": "copernicus_dem_glo30",
                "reuse_existing_raw_mosaic": True,
            }
    except OSError:
        pass

    min_x, min_y, max_x, max_y = ctx.bbox
    lat_start = math.floor(min_y)
    lat_end = math.ceil(max_y)
    lon_start = math.floor(min_x)
    lon_end = math.ceil(max_x)

    # NOTE: Copernicus GLO-30 is distributed as 1°x1° tiles keyed by the SW corner.
    # We compute tiles from the AOI bbox. Some tiles are legitimately unavailable from
    # the public Copernicus S3 endpoint (commonly over ocean). We SKIP unavailable tiles
    # and let the AOI-bounded mosaic carry NoData where coverage is missing.
    tile_defs: List[Tuple[str, int, int]] = []
    for lat in range(lat_start, lat_end):
        for lon in range(lon_start, lon_end):
            tile_defs.append((_copernicus_tile_name(lat, lon), lat, lon))

    if not tile_defs:
        raise RuntimeError("Unable to determine Copernicus DEM tiles for AOI")

    download_dir = Path(tempfile.mkdtemp(prefix=f"copdem_{job.id}_"))
    tile_paths: List[Path] = []
    downloaded_tiles: List[str] = []
    skipped_tiles: List[str] = []
    total_tiles = len(tile_defs)
    try:
        progress_lock = threading.Lock()
        progress_state: Dict[str, int] = {"done": 0}

        with ThreadPoolExecutor(max_workers=MAX_TILE_CONCURRENCY) as pool:
            futures = {
                pool.submit(
                    _download_single_copernicus_tile,
                    td, download_dir, job, ctx, total_tiles,
                    progress_state, progress_lock,
                ): td
                for td in tile_defs
            }
            for future in as_completed(futures):
                tile, dest, skipped = future.result()
                if dest:
                    tile_paths.append(dest)
                    downloaded_tiles.append(tile)
                elif skipped:
                    skipped_tiles.append(tile)

        if not tile_paths:
            raise RuntimeError("No Copernicus DEM tiles were downloaded")

        temp_output = raw_path.with_suffix(".tmp.tif")
        if temp_output.exists():
            temp_output.unlink()
        # IMPORTANT: use GTiff for raw mosaic output. The COG driver can be expensive for
        # very large mosaics and may create nested temp files; we keep raw as a tiled,
        # compressed BigTIFF and let downstream processing handle project-CRS clipping.
        warp_cmd = [
            GDALWARP_BIN,
            "-overwrite",
            "-of",
            "GTiff",
            "-co",
            "COMPRESS=LZW",
            "-co",
            "TILED=YES",
            "-co",
            "BIGTIFF=IF_SAFER",
            "-co",
            "SPARSE_OK=TRUE",
            "-t_srs",
            "EPSG:4326",
            "-srcnodata",
            "-32768",
            "-dstnodata",
            "-32768",
            "-te",
            str(min_x),
            str(min_y),
            str(max_x),
            str(max_y),
            "-tr",
            "0.0002777778",
            "0.0002777778",
            "-wo",
            "NUM_THREADS=ALL_CPUS",
            "-multi",
            "-r",
            "bilinear",
            *[str(path) for path in tile_paths],
            str(temp_output),
        ]
        _run_command(warp_cmd, ctx.project_path, job, ctx, "Copernicus mosaic")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.exists():
            raw_path.unlink()
        temp_output.replace(raw_path)
        if len(getattr(job, "categories", []) or []) == 1:
            with JOB_LOCK:
                job.progress = max(float(job.progress or 0.0), 0.92)
                job.updated_at = _utc_now()
        info: Dict[str, object] = {
            "tiles_downloaded": downloaded_tiles,
            "tiles_skipped_unavailable": skipped_tiles,
        }
        if skipped_tiles:
            info["note"] = (
                "Some Copernicus DEM tiles were unavailable from the public Copernicus S3 endpoint "
                "(often over ocean). They were skipped and will appear as NoData in the raw mosaic."
            )
        return ["copernicus_dem_fetch", f"tiles_ok={len(tile_paths)}", f"tiles_skipped={len(skipped_tiles)}"], info
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


def _opentopo_3dep_fetch(ctx: FetchContext, raw_path: Path, job: DatasetJobState) -> Tuple[List[str], Dict[str, object]]:
    """
    Fetch USGS 3DEP 1m LiDAR DEM from USGS TNM API.
    
    Downloads all tiles that intersect the AOI and mosaics them together.
    Falls back to OpenTopography SRTM if 3DEP 1m is not available.
    """
    west, south, east, north = ctx.bbox
    
    _log_to_job(job, ctx, f"Fetching USGS 3DEP 1m DEM for bbox: {ctx.bbox_string}")
    
    download_dir = Path(tempfile.mkdtemp(prefix=f"3dep_{job.id}_"))
    
    try:
        # Query USGS TNM API for 3DEP 1m products
        tnm_api_url = (
            f"https://tnmaccess.nationalmap.gov/api/v1/products"
            f"?datasets=Digital%20Elevation%20Model%20(DEM)%201%20meter"
            f"&bbox={west},{south},{east},{north}"
            f"&outputFormat=JSON"
        )
        
        _log_to_job(job, ctx, f"Querying USGS TNM API for 3DEP 1m products...")
        
        query_cmd = ["curl", "-sSfL", tnm_api_url, "-o", str(download_dir / "tnm_response.json")]
        _run_command(query_cmd, ctx.project_path, job, ctx, "TNM API query")
        
        response_file = download_dir / "tnm_response.json"
        if not response_file.exists():
            raise RuntimeError("TNM API query failed - no response file")
        
        import json as json_module
        with open(response_file) as f:
            tnm_data = json_module.load(f)
        
        items = tnm_data.get("items", [])
        if not items:
            _log_to_job(job, ctx, "No 3DEP 1m products found in TNM API for this AOI.")
            _log_to_job(job, ctx, "Trying OpenTopography SRTM as fallback (30m resolution)...")
            
            # Fallback to OpenTopography SRTM GL1
            opentopo_url = (
                f"https://portal.opentopography.org/API/globaldem"
                f"?demtype=SRTMGL1"
                f"&south={south}&north={north}&west={west}&east={east}"
                f"&outputFormat=GTiff"
                f"&API_Key=demoapikeyot2022"
            )
            
            dem_file = download_dir / "srtm_dem.tif"
            download_cmd = ["curl", "-sSfL", opentopo_url, "-o", str(dem_file)]
            _run_command(download_cmd, ctx.project_path, job, ctx, "OpenTopography SRTM download")
            
            if not dem_file.exists() or dem_file.stat().st_size < 1000:
                raise RuntimeError("OpenTopography SRTM download failed or returned empty file")
            
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(dem_file, raw_path)
            
            return ["opentopo_srtm_fetch"], {
                "source": "OpenTopography SRTM GL1",
                "resolution_m": 30,
                "note": "3DEP 1m not available for this AOI, using SRTM 30m"
            }
        
        # Download ALL tiles that intersect the AOI
        _log_to_job(job, ctx, f"Found {len(items)} 3DEP 1m tiles covering the AOI")
        
        tile_files: List[Path] = []
        for idx, item in enumerate(items):
            download_url = item.get("downloadURL")
            if not download_url:
                _log_to_job(job, ctx, f"Skipping tile {idx+1} - no download URL")
                continue
            
            title = item.get("title", f"tile_{idx}")
            _log_to_job(job, ctx, f"Downloading tile {idx+1}/{len(items)}: {title}")
            
            tile_file = download_dir / f"tile_{idx}.tif"
            download_cmd = ["curl", "-sSfL", "--connect-timeout", "30", "--max-time", "300", download_url, "-o", str(tile_file)]
            
            try:
                _run_command(download_cmd, ctx.project_path, job, ctx, f"3DEP tile {idx+1} download")
                if tile_file.exists() and tile_file.stat().st_size > 1000:
                    tile_files.append(tile_file)
                else:
                    _log_to_job(job, ctx, f"Tile {idx+1} download failed or empty")
            except RuntimeError as e:
                _log_to_job(job, ctx, f"Failed to download tile {idx+1}: {e}")
        
        if not tile_files:
            raise RuntimeError("No 3DEP tiles were successfully downloaded")
        
        _log_to_job(job, ctx, f"Successfully downloaded {len(tile_files)} tiles")
        
        # Mosaic tiles and clip to AOI in one step
        # Note: 3DEP tiles are in UTM (NAD83), so we need to specify -te_srs for the bbox
        mosaic_file = download_dir / "3dep_mosaic.tif"
        
        warp_cmd = [
            GDALWARP_BIN,
            "-overwrite",
            "-of", "GTiff",
            "-t_srs", "EPSG:4326",  # Output in WGS84 for consistency
            "-te", str(west), str(south), str(east), str(north),
            "-te_srs", "EPSG:4326",  # Bbox is in WGS84
            "-r", "bilinear",
            "-co", "COMPRESS=LZW",
            "-co", "TILED=YES",
        ] + [str(f) for f in tile_files] + [str(mosaic_file)]
        
        _run_command(warp_cmd, ctx.project_path, job, ctx, "3DEP mosaic and clip")
        
        if not mosaic_file.exists() or mosaic_file.stat().st_size < 1000:
            raise RuntimeError("3DEP mosaic/clip failed or produced empty file")
        
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.exists():
            raw_path.unlink()
        shutil.copy(mosaic_file, raw_path)
        
        _log_to_job(job, ctx, f"3DEP 1m DEM successfully fetched: {raw_path.stat().st_size} bytes")
        
        return ["usgs_3dep_1m_fetch"], {
            "source": "USGS 3DEP 1m LiDAR",
            "tiles_downloaded": len(tile_files),
            "product_titles": [item.get("title") for item in items[:len(tile_files)]],
            "resolution_m": 1,
        }
        
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


def _zeus_osm_fetch(dataset_key: str, ctx: FetchContext, raw_path: Path, job: DatasetJobState) -> Tuple[List[str], Dict[str, object]]:
    tool_map = {
        "roads": "osm_roads_fetch",
        "railways": "osm_railways_fetch",
        "powerlines": "osm_power_fetch",
        "waterways": "osm_waterways_fetch",
    }
    tool = tool_map.get(dataset_key)
    if not tool:
        raise RuntimeError(f"No ZEUS OSM tool configured for dataset '{dataset_key}'")

    cmd = [
        str(ZEUS_BIN),
        "tools",
        tool,
        "--aoi",
        str(ctx.aoi_file),
        "--output",
        str(raw_path),
        "--overwrite",
    ]
    _run_command(cmd, ctx.project_path, job, ctx, f"OSM {dataset_key} fetch (ZEUS tool)")
    return [tool], {}


def _osm_overpass_fetch(dataset_key: str, ctx: FetchContext, raw_path: Path, job: DatasetJobState) -> Tuple[List[str], Dict[str, object]]:
    """
    Fetch OSM data with proper attribute expansion for dataset compatibility.
    Uses JSON output format and Python processing to extract/compute all required fields.
    """
    filters = OSM_OVERPASS_FILTERS.get(dataset_key)
    if not filters:
        raise RuntimeError(f"No Overpass filters configured for dataset '{dataset_key}'")

    south, west, north, east = ctx.bbox[1], ctx.bbox[0], ctx.bbox[3], ctx.bbox[2]
    filter_body = "\n  ".join(f"{expr}({south},{west},{north},{east});" for expr in filters)
    
    # Use JSON format with geometry for proper attribute extraction
    query = f"""
[out:json][timeout:900];
(
  {filter_body}
);
out geom;
""".strip()

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"osm_{dataset_key}_{job.id}_"))
    json_file = tmp_dir / f"{dataset_key}.json"
    geojson_file = tmp_dir / f"{dataset_key}.geojson"
    
    try:
        # Download from Overpass API
        last_error: Optional[Exception] = None
        for endpoint in OVERPASS_ENDPOINTS:
            download_cmd = [
                "curl",
                "-sSfL",
                "-X",
                "POST",
                "-d",
                query,
                endpoint,
                "-o",
                str(json_file),
            ]
            try:
                _run_command(download_cmd, ctx.project_path, job, ctx, f"OSM {dataset_key} download")
                last_error = None
                break
            except RuntimeError as exc:  # noqa: BLE001
                last_error = exc
                _log_to_job(job, ctx, f"Overpass endpoint {endpoint} failed: {exc}")
                continue

        if last_error:
            raise RuntimeError(f"OSM {dataset_key} download failed: {last_error}")

        # Process JSON to GeoJSON with proper attribute schema
        _log_to_job(job, ctx, f"Processing OSM {dataset_key} with attribute expansion...")
        _process_osm_json_to_geojson(dataset_key, json_file, geojson_file, job, ctx)

        # Convert GeoJSON to GeoPackage
        layer_name = _get_osm_layer_name(dataset_key)
        convert_cmd = [
            OGR2OGR_BIN,
            "-f",
            "GPKG",
            "-overwrite",
            "-nln",
            layer_name,
            "-a_srs",
            "EPSG:4326",
            str(raw_path),
            str(geojson_file),
        ]
        _run_command(convert_cmd, ctx.project_path, job, ctx, f"OSM {dataset_key} conversion")
        return [f"osm_{dataset_key}_overpass_fetch"], {}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _ensure_can_indigenous_lands_db(
    *,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[Path, Dict[str, object]]:
    """
    Ensure the Canada Indigenous/Aboriginal lands raw dataset exists in the global DB cache.

    Raw is stored exactly as acquired (GeoJSON from the provider's ArcGIS REST endpoint).
    No project-specific processing (no clipping, no cleaning) occurs here.
    """
    db_dir = DBS_ROOT / CAN_INDIGENOUS_LANDS_DB_ID
    raw_dir = db_dir / "raw"
    raw_path = raw_dir / CAN_INDIGENOUS_LANDS_DB_RAW_FILENAME
    db_json_path = db_dir / "db.json"

    DBS_ROOT.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    with DB_LOCK:
        # Fast-path: existing + checksum matches recorded db.json
        if raw_path.exists() and db_json_path.exists():
            try:
                payload = json.loads(db_json_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    raw_files = payload.get("raw_files") or []
                    if isinstance(raw_files, list) and raw_files:
                        recorded = raw_files[0]
                        recorded_sha = str((recorded or {}).get("sha256") or "").strip()
                        recorded_size = int((recorded or {}).get("size_bytes") or 0)
                        if recorded_sha and recorded_size > 0:
                            size = raw_path.stat().st_size
                            if size == recorded_size and _sha256_file(raw_path) == recorded_sha:
                                if log:
                                    log(f"DB cache hit: {CAN_INDIGENOUS_LANDS_DB_ID} (sha256 verified)")
                                return raw_path, payload
            except Exception:  # noqa: BLE001
                # Fall through to rebuild the DB entry
                pass

        if log:
            log(f"Populating DB cache entry '{CAN_INDIGENOUS_LANDS_DB_ID}' (first-time download)...")

        layer_url = CLSS_ABORIGINAL_LANDS_LAYER_URL.rstrip("/")
        query_url = layer_url + "/query"

        # Pull full objectId list (no spatial filter) for national dataset caching.
        ids_payload = _http_get_json(
            query_url,
            {"f": "json", "where": "1=1", "returnIdsOnly": "true"},
            timeout=120,
        )
        object_ids = ids_payload.get("objectIds") or []
        object_ids = [int(v) for v in object_ids if v is not None]
        if not object_ids:
            raise RuntimeError("ArcGIS layer returned no objectIds for Indigenous lands.")

        if log:
            log(f"CLSS Aboriginal lands: {len(object_ids)} features (objectIds)")

        # Download as GeoJSON in chunks (provider enforces maxRecordCount).
        features: List[Dict[str, Any]] = []
        # Use smaller chunks to avoid long-URL limits on some ArcGIS proxy frontends.
        chunk_size = 100
        for start in range(0, len(object_ids), chunk_size):
            ids_chunk = object_ids[start : start + chunk_size]
            params = {
                "f": "geojson",
                "objectIds": ",".join(str(v) for v in ids_chunk),
                "outFields": "*",
                "returnGeometry": "true",
            }
            data = _http_get_json(query_url, params, timeout=180)
            chunk_features = data.get("features") or []
            if not isinstance(chunk_features, list):
                chunk_features = []
            features.extend(chunk_features)  # type: ignore[arg-type]
            if log:
                log(f"Downloaded {min(start + len(ids_chunk), len(object_ids))}/{len(object_ids)} features...")

        fc = {"type": "FeatureCollection", "features": features}

        tmp = raw_path.with_suffix(raw_path.suffix + ".tmp")
        tmp.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(raw_path)

        sha256 = _sha256_file(raw_path)
        size_bytes = raw_path.stat().st_size
        acquired_utc = _utc_iso()

        payload = {
            "db_id": CAN_INDIGENOUS_LANDS_DB_ID,
            "dataset_name": "Aboriginal Lands of Canada Legislative Boundaries",
            "provider": "Natural Resources Canada (NRCan) — Canada Lands Survey System (CLSS)",
            "provider_url": "https://clss.nrcan-rncan.gc.ca",
            "source": "CLSS Administrative Boundaries (ArcGIS MapServer layer 0)",
            "source_url": CLSS_ABORIGINAL_LANDS_LAYER_URL,
            "license": "Open Government Licence - Canada",
            "attribution": "Natural Resources Canada (NRCan) — Canada Lands Survey System (CLSS).",
            "coverage_date": "Current (monthly updates)",
            "acquired_utc": acquired_utc,
            "raw_files": [
                {
                    "path": str(raw_path.relative_to(db_dir)),
                    "sha256": sha256,
                    "size_bytes": int(size_bytes),
                    "format": "GeoJSON",
                }
            ],
            "notes": "Stored as provider GeoJSON output (no clipping, no cleaning).",
        }

        _write_json(db_json_path, payload)
        _upsert_db_index_row(
            {
                "db_id": CAN_INDIGENOUS_LANDS_DB_ID,
                "dataset_name": str(payload.get("dataset_name") or ""),
                "provider": str(payload.get("provider") or ""),
                "provider_url": str(payload.get("provider_url") or ""),
                "source": str(payload.get("source") or ""),
                "source_url": str(payload.get("source_url") or ""),
                "license": str(payload.get("license") or ""),
                "attribution": str(payload.get("attribution") or ""),
                "raw_relpath": str(raw_path.relative_to(DBS_ROOT)),
                "sha256": sha256,
                "size_bytes": str(int(size_bytes)),
                "acquired_utc": acquired_utc,
            }
        )

        if log:
            log(f"DB cache populated: {raw_path} ({size_bytes} bytes, sha256={sha256[:12]}...)")
        return raw_path, payload


def _arcgis_object_ids(
    layer_url: str,
    bbox: Tuple[float, float, float, float],
    *,
    where: str = "1=1",
) -> List[int]:
    west, south, east, north = bbox
    query_url = layer_url.rstrip("/") + "/query"
    params = {
        "f": "json",
        "where": where,
        "returnIdsOnly": "true",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    data = _http_get_json(query_url, params)
    ids = data.get("objectIds") or []
    return [int(v) for v in ids]


def _arcgis_fetch_geojson_to_gpkg(
    layer_url: str,
    ctx: FetchContext,
    output_gpkg: Path,
    job: DatasetJobState,
    *,
    layer_name: str,
    where: str = "1=1",
    chunk_size: int = 500,
) -> Dict[str, object]:
    """
    Fetch an ArcGIS REST layer intersecting the project's AOI bbox and write it as a GeoPackage.

    - Uses returnIdsOnly + objectIds chunking to avoid pagination assumptions.
    - Requests f=geojson with outSR=4326 for lossless downstream processing in ZEUS.
    """
    object_ids = _arcgis_object_ids(layer_url, ctx.bbox, where=where)
    if not object_ids:
        # Valid outcome: no features intersect AOI.
        # Create an empty GeoPackage with the expected layer name.
        # ogr2ogr can create an empty dataset from an empty GeoJSON.
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"arcgis_empty_{job.id}_"))
        try:
            empty_geojson = tmp_dir / "empty.geojson"
            empty_geojson.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
            cmd = [
                OGR2OGR_BIN,
                "-f",
                "GPKG",
                "-overwrite",
                "-nln",
                layer_name,
                "-a_srs",
                "EPSG:4326",
                str(output_gpkg),
                str(empty_geojson),
            ]
            _run_command(cmd, ctx.project_path, job, ctx, f"ArcGIS {layer_name} empty init")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"feature_count": 0, "object_ids": [], "chunks": 0}

    query_url = layer_url.rstrip("/") + "/query"
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"arcgis_{job.id}_{layer_name}_"))
    chunks = 0
    try:
        # Ensure clean output
        if output_gpkg.exists():
            output_gpkg.unlink()

        for start in range(0, len(object_ids), chunk_size):
            chunks += 1
            ids_chunk = object_ids[start : start + chunk_size]
            params = {
                "f": "geojson",
                "objectIds": ",".join(str(v) for v in ids_chunk),
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
            }
            data = _http_get_json(query_url, params, timeout=120)
            geojson_path = tmp_dir / f"chunk_{chunks}.geojson"
            geojson_path.write_text(json.dumps(data), encoding="utf-8")

            if chunks == 1:
                cmd = [
                    OGR2OGR_BIN,
                    "-f",
                    "GPKG",
                    "-overwrite",
                    "-nln",
                    layer_name,
                    "-a_srs",
                    "EPSG:4326",
                    "-nlt",
                    "PROMOTE_TO_MULTI",
                    str(output_gpkg),
                    str(geojson_path),
                ]
            else:
                cmd = [
                    OGR2OGR_BIN,
                    "-f",
                    "GPKG",
                    "-update",
                    "-append",
                    "-nln",
                    layer_name,
                    "-nlt",
                    "PROMOTE_TO_MULTI",
                    str(output_gpkg),
                    str(geojson_path),
                ]
            _run_command(cmd, ctx.project_path, job, ctx, f"ArcGIS {layer_name} ingest (chunk {chunks})")

        return {"feature_count": len(object_ids), "object_ids": object_ids, "chunks": chunks}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _cer_pipelines_fetch(ctx: FetchContext, raw_path: Path, job: DatasetJobState) -> Tuple[List[str], Dict[str, object]]:
    _log_to_job(job, ctx, "Fetching CER pipeline systems (federal regulated pipelines) via ArcGIS FeatureServer...")
    info = _arcgis_fetch_geojson_to_gpkg(
        CER_PIPELINES_LAYER_URL,
        ctx,
        raw_path,
        job,
        layer_name="pipelines",
        chunk_size=500,
    )
    return ["cer_pipelines_fetch", f"features={info.get('feature_count', 0)}"], {
        "fetch_tool": "cer_pipelines_fetch",
        "dataset_name": "CER Regulated Pipelines (Pipeline Systems)",
        "source": "Canada Energy Regulator (CER) pipeline systems (public ArcGIS service)",
        "provider": "Canada Energy Regulator",
        "provider_url": "https://www.cer-rec.gc.ca/",
        "documentation_url": "https://www.cer-rec.gc.ca/en/safety-environment/industry-performance/interactive-pipeline/",
        # NOTE: CER service does not expose explicit license text in ArcGIS item metadata.
        "license": "Public data via CER interactive pipeline map (verify CER terms/disclaimer for reuse)",
        "attribution": "© Canada Energy Regulator (CER).",
        "coverage_date": "Current",
        "notes": "Federally regulated pipelines only; does not include provincially regulated pipelines.",
    }


def _cpcad_protected_areas_fetch(ctx: FetchContext, raw_path: Path, job: DatasetJobState) -> Tuple[List[str], Dict[str, object]]:
    _log_to_job(job, ctx, "Fetching CPCAD protected and conserved areas via ECCC ArcGIS MapServer...")
    info = _arcgis_fetch_geojson_to_gpkg(
        CPCAD_LAYER_URL,
        ctx,
        raw_path,
        job,
        layer_name="protected_areas",
        chunk_size=1000,
    )
    return ["cpcad_fetch", f"features={info.get('feature_count', 0)}"], {
        "fetch_tool": "cpcad_fetch",
        "dataset_name": "Canadian Protected and Conserved Areas Database (CPCAD)",
        "source": "ECCC CPCAD (ArcGIS MapServer)",
        "provider": "Environment and Climate Change Canada (ECCC)",
        "provider_url": "https://www.canada.ca/en/environment-climate-change.html",
        "documentation_url": "https://www.canada.ca/en/environment-climate-change/services/national-wildlife-areas/protected-conserved-areas-database.html",
        "license": "Open Government Licence - Canada",
        "attribution": "Environment and Climate Change Canada (ECCC) — Canadian Protected and Conserved Areas Database (CPCAD).",
        "coverage_date": "Current (regular updates)",
        "notes": "Authoritative protected and conserved areas for Canada (PA + OECM).",
    }


def _can_indigenous_lands_fetch(ctx: FetchContext, raw_path: Path, job: DatasetJobState) -> Tuple[List[str], Dict[str, object]]:
    """
    Fetch Canada Indigenous/Aboriginal lands via the global DB cache.

    This keeps the project's pipeline protocol unchanged: it still has a project-local raw artifact
    (materialized as a symlink/hardlink/copy into the staging directory), then the normal
    project-specific processing (clip/reproject) runs as usual.
    """
    _log_to_job(job, ctx, "Using global DB cache for Canada Indigenous/Aboriginal lands (CLSS)...")

    db_raw_path, db_meta = _ensure_can_indigenous_lands_db(log=lambda m: _log_to_job(job, ctx, m))
    mode_used = _materialize_db_raw(db_raw_path, raw_path, log=lambda m: _log_to_job(job, ctx, m))

    # Pull sha/size from db.json payload for provenance.
    db_sha = None
    db_size = None
    try:
        raw_files = (db_meta.get("raw_files") or []) if isinstance(db_meta, dict) else []
        if isinstance(raw_files, list) and raw_files:
            first = raw_files[0] if isinstance(raw_files[0], dict) else {}
            db_sha = (first.get("sha256") if isinstance(first, dict) else None) or None
            db_size = (first.get("size_bytes") if isinstance(first, dict) else None) or None
    except Exception:  # noqa: BLE001
        db_sha = None
        db_size = None

    return ["db_cache_materialize", CAN_INDIGENOUS_LANDS_DB_ID, f"mode={mode_used}"], {
        "fetch_tool": "db_indigenous_lands_fetch",
        "dataset_name": "Aboriginal Lands of Canada Legislative Boundaries",
        "source": "NRCan CLSS Administrative Boundaries (local DB cache)",
        "provider": "Natural Resources Canada (NRCan) — Canada Lands Survey System (CLSS)",
        "provider_url": "https://clss.nrcan-rncan.gc.ca",
        "documentation_url": "https://natural-resources.canada.ca/maps-tools-publications/maps/boundaries-land-surveys/tools-applications-canada-lands-surveys",
        "license": "Open Government Licence - Canada",
        "attribution": "Natural Resources Canada (NRCan) — Canada Lands Survey System (CLSS).",
        "coverage_date": "Current (monthly updates)",
        "db_id": CAN_INDIGENOUS_LANDS_DB_ID,
        "db_path": str(db_raw_path),
        "db_sha256": db_sha,
        "db_size_bytes": db_size,
        "db_materialize_mode": mode_used,
        "db_acquired_utc": (db_meta.get("acquired_utc") if isinstance(db_meta, dict) else None),
        "db_source_url": (db_meta.get("source_url") if isinstance(db_meta, dict) else None),
    }


def _nhn_waterways_fetch(ctx: FetchContext, raw_path: Path, job: DatasetJobState) -> Tuple[List[str], Dict[str, object]]:
    """
    Fetch NRCan National Hydro Network (NHN) waterway lines for the AOI using tile-based work units.

    Strategy:
    - Use the NHN work-unit index (zipped shapefile) to select intersecting work units by AOI bbox.
    - Download only the required work-unit ZIPs from the NHN shapefile directory.
    - Extract/merge the NHN Network Linear Flow layer (HN_NLFLOW_1) across work units into one raw GeoPackage.

    Raw output remains in source CRS (typically EPSG:4617 NAD83(CSRS)) and may extend beyond the AOI polygon
    because full work units are downloaded. Processing stage clips to AOI and reprojects to project CRS.
    """
    _log_to_job(job, ctx, "Fetching NHN waterways (tile-based) from NRCan GeoBase via FTP work-unit ZIPs...")

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"nhn_{job.id}_"))
    try:
        import zipfile

        # 1) Extract intersecting work units from the index (by AOI bbox)
        index_geojson = tmp_dir / "nhn_index_sel.geojson"
        minx, miny, maxx, maxy = ctx.bbox
        ogr_cmd = [
            OGR2OGR_BIN,
            "-f",
            "GeoJSON",
            str(index_geojson),
            f"/vsizip/vsicurl/{NHN_INDEX_ZIP_URL}",
            "-spat",
            str(minx),
            str(miny),
            str(maxx),
            str(maxy),
        ]
        _run_command(ogr_cmd, ctx.project_path, job, ctx, "NHN index spatial filter")
        index = json.loads(index_geojson.read_text(encoding="utf-8"))
        dataset_names = []
        for feature in index.get("features", []):
            props = feature.get("properties") or {}
            code = (props.get("DATASETNAM") or "").strip()
            if code:
                dataset_names.append(code)
        # Deduplicate (preserve order)
        seen: set = set()
        tiles: List[str] = []
        for code in dataset_names:
            if code not in seen:
                seen.add(code)
                tiles.append(code)

        if not tiles:
            _log_to_job(job, ctx, "NHN tile selection returned 0 work units for AOI bbox; writing empty waterways dataset.")
            empty_geojson = tmp_dir / "empty.geojson"
            empty_geojson.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
            _run_command(
                [
                    OGR2OGR_BIN,
                    "-f",
                    "GPKG",
                    "-overwrite",
                    "-nln",
                    "waterways",
                    "-a_srs",
                    "EPSG:4617",
                    str(raw_path),
                    str(empty_geojson),
                ],
                ctx.project_path,
                job,
                ctx,
                "NHN waterways empty init",
            )
            return ["nhn_waterways_fetch", "tiles=0"], {"tiles_downloaded": []}

        _log_to_job(job, ctx, f"NHN work units selected: {len(tiles)}")

        # 2) Download each work unit ZIP and append the NLFLOW layer into a single GPKG
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.exists():
            raw_path.unlink()

        tiles_used: List[str] = []
        tiles_skipped_no_nlflow: List[str] = []
        wrote_any = False

        for idx, code in enumerate(tiles, start=1):
            code_upper = code.upper()
            code_lower = code.lower()
            subdir = code_lower[:2]
            zip_name = f"nhn_rhn_{code_lower}_shp_en.zip"
            zip_url = f"{NHN_TILE_BASE_URL}/{subdir}/{zip_name}"
            zip_path = tmp_dir / zip_name
            _run_command(
                ["curl", "-sSfL", "--connect-timeout", "30", "--max-time", "600", zip_url, "-o", str(zip_path)],
                ctx.project_path,
                job,
                ctx,
                f"NHN tile download {idx}/{len(tiles)}",
            )
            if not zip_path.exists() or zip_path.stat().st_size < 1024:
                raise RuntimeError(f"NHN tile download failed or empty for {code} ({zip_url})")

            # Read NLFLOW shapefile directly from ZIP without extracting.
            # The NHN schema/version segment varies (e.g., `_3_0_...`) so we locate the layer
            # by scanning the ZIP contents instead of hardcoding the filename.
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    members = zf.namelist()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Unable to read NHN work-unit ZIP '{zip_name}': {exc}")

            candidates = [
                name
                for name in members
                if name.upper().endswith(".SHP")
                and "HN_NLFLOW_1" in name.upper()
                and code_upper in name.upper()
            ]
            if not candidates:
                # Fallback: any NLFLOW layer in the ZIP
                candidates = [
                    name
                    for name in members
                    if name.upper().endswith(".SHP") and "HN_NLFLOW_1" in name.upper()
                ]
            if not candidates:
                # Some work units (e.g., coastal-only) legitimately do not include the NLFLOW layer.
                # Treat these as a non-fatal skip so the dataset can still be built.
                tiles_skipped_no_nlflow.append(code)
                sample = ", ".join(members[:12])
                _log_to_job(
                    job,
                    ctx,
                    f"NHN work unit {code}: NLFLOW layer not found in '{zip_name}' (sample: {sample}) — skipping.",
                )
                continue
            internal_shp = candidates[0]
            shp_path = f"/vsizip/{zip_path}/{internal_shp}"

            if not wrote_any:
                cmd = [
                    OGR2OGR_BIN,
                    "-f",
                    "GPKG",
                    "-overwrite",
                    "-nln",
                    "waterways",
                    "-nlt",
                    "PROMOTE_TO_MULTI",
                    str(raw_path),
                    shp_path,
                ]
            else:
                cmd = [
                    OGR2OGR_BIN,
                    "-f",
                    "GPKG",
                    "-update",
                    "-append",
                    "-nln",
                    "waterways",
                    "-nlt",
                    "PROMOTE_TO_MULTI",
                    str(raw_path),
                    shp_path,
                ]
            _run_command(cmd, ctx.project_path, job, ctx, f"NHN waterways merge {idx}/{len(tiles)}")
            wrote_any = True
            tiles_used.append(code)

        if not wrote_any:
            # We had work units, but none contained NLFLOW; write an empty dataset instead of failing.
            _log_to_job(job, ctx, "NHN work units contained no NLFLOW layers; writing empty waterways dataset.")
            empty_geojson = tmp_dir / "empty.geojson"
            empty_geojson.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
            _run_command(
                [
                    OGR2OGR_BIN,
                    "-f",
                    "GPKG",
                    "-overwrite",
                    "-nln",
                    "waterways",
                    "-a_srs",
                    "EPSG:4617",
                    str(raw_path),
                    str(empty_geojson),
                ],
                ctx.project_path,
                job,
                ctx,
                "NHN waterways empty init (no NLFLOW)",
            )

        return ["nhn_waterways_fetch", f"work_units={len(tiles)}", f"nlflow_used={len(tiles_used)}"], {
            "tiles_downloaded": tiles,
            "tiles_used_nlflow": tiles_used,
            "tiles_skipped_missing_nlflow": tiles_skipped_no_nlflow,
            "fetch_tool": "nhn_waterways_fetch",
            "dataset_name": "NRCan National Hydro Network (NHN) — Network Linear Flow",
            "source": "NRCan GeoBase NHN (work-unit shapefiles)",
            "provider": "Natural Resources Canada (NRCan)",
            "provider_url": "https://open.canada.ca/",
            "documentation_url": "https://open.canada.ca/data/en/dataset/a4b190fe-e090-4e6d-881e-b87956c07977",
            "license": "Open Government Licence - Canada",
            "attribution": "Natural Resources Canada (NRCan) — GeoBase National Hydro Network (NHN).",
            "coverage_date": "Current (GeoBase NHN; annual or better)",
            "notes": "Tile-based work units selected by AOI bbox; raw includes full work units and may extend beyond AOI polygon.",
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# OSM processing helpers
# ---------------------------------------------------------------------------

def _get_osm_layer_name(dataset_key: str) -> str:
    """Map dataset key to proper layer name."""
    layer_names = {
        "roads": "roads",
        "railways": "railways",
        "powerlines": "power_lines",
        "waterways": "waterways",
        "pipelines": "pipelines",
    }
    return layer_names.get(dataset_key, dataset_key)


def _process_osm_json_to_geojson(
    dataset_key: str,
    json_file: Path,
    geojson_file: Path,
    job: DatasetJobState,
    ctx: FetchContext,
) -> None:
    """
    Process Overpass JSON response to GeoJSON with proper attribute schema.
    Implements the same attribute expansion logic as ZEUS CLI tools.
    """
    with open(json_file, "r") as f:
        data = json.load(f)
    
    features = []
    for element in data.get("elements", []):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        
        coords = [[pt["lon"], pt["lat"]] for pt in element["geometry"]]
        if len(coords) < 2:
            continue
        
        tags = element.get("tags", {})
        osm_id = element.get("id")
        
        # Build properties based on dataset type
        if dataset_key == "roads":
            properties = _build_roads_properties(osm_id, tags)
        elif dataset_key == "railways":
            properties = _build_railways_properties(osm_id, tags)
        elif dataset_key == "waterways":
            properties = _build_waterways_properties(osm_id, tags)
        elif dataset_key == "powerlines":
            properties = _build_powerlines_properties(osm_id, tags)
        elif dataset_key == "pipelines":
            properties = _build_pipelines_properties(osm_id, tags)
        else:
            properties = {"osm_id": osm_id, "name": tags.get("name", "")}
        
        feature = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": properties,
        }
        features.append(feature)
    
    geojson = {"type": "FeatureCollection", "features": features}
    
    with open(geojson_file, "w") as f:
        json.dump(geojson, f)
    
    _log_to_job(job, ctx, f"Processed {len(features)} {dataset_key} features with expanded attributes")


# ---------------------------------------------------------------------------
# OSM property builders
# ---------------------------------------------------------------------------

def _build_roads_properties(osm_id: int, tags: Dict[str, str]) -> Dict[str, Any]:
    """Build roads properties with proper schema."""
    return {
        "osm_id": osm_id,
        "name": tags.get("name", ""),
        "highway": tags.get("highway", ""),
        "ref": tags.get("ref", ""),
        "surface": tags.get("surface", ""),
        "lanes": tags.get("lanes", ""),
        "maxspeed": tags.get("maxspeed", ""),
        "oneway": tags.get("oneway", ""),
    }


def _build_railways_properties(osm_id: int, tags: Dict[str, str]) -> Dict[str, Any]:
    """Build railways properties with proper schema."""
    return {
        "osm_id": osm_id,
        "name": tags.get("name", ""),
        "railway": tags.get("railway", ""),
        "operator": tags.get("operator", ""),
        "gauge": tags.get("gauge", ""),
        "electrified": tags.get("electrified", ""),
        "usage": tags.get("usage", ""),
        "service": tags.get("service", ""),
    }


def _build_waterways_properties(osm_id: int, tags: Dict[str, str]) -> Dict[str, Any]:
    """Build waterways properties with width computation logic."""
    waterway_type = tags.get("waterway", "")
    width_raw = tags.get("width", "")
    
    # Parse width from tag
    width_m: Optional[float] = None
    if width_raw:
        try:
            width_m = float(width_raw.split()[0])
        except (ValueError, IndexError):
            pass
    
    # Estimate from waterway type if not tagged
    if width_m is None:
        if waterway_type in ("stream", "ditch"):
            width_m = 2.0  # 1-3m typical
        elif waterway_type == "drain":
            width_m = 5.0  # 3-10m typical
        elif waterway_type == "canal":
            width_m = 15.0  # 10-50m typical
        else:  # river or other
            width_m = 25.0  # default for rivers
    
    # Compute width_class and crossing_cost_cat
    if width_m < 3:
        width_class = "small"
        crossing_cost_cat = "low"  # $10K-20K open cut
    elif width_m < 10:
        width_class = "medium"
        crossing_cost_cat = "medium"  # $30K-70K open cut
    elif width_m < 50:
        width_class = "large"
        crossing_cost_cat = "high"  # $200K-400K HDD
    else:
        width_class = "major"
        crossing_cost_cat = "very_high"  # $800K+ HDD
    
    return {
        "osm_id": osm_id,
        "name": tags.get("name", ""),
        "waterway": waterway_type,
        "width": width_raw,
        "width_m": width_m,
        "width_class": width_class,
        "crossing_cost_cat": crossing_cost_cat,
        "depth": tags.get("depth", ""),
        "seasonal": tags.get("seasonal", ""),
        "intermittent": tags.get("intermittent", ""),
        "tunnel": tags.get("tunnel", ""),
    }


def _build_powerlines_properties(osm_id: int, tags: Dict[str, str]) -> Dict[str, Any]:
    """Build power lines properties with voltage computation logic."""
    power_type = tags.get("power", "")
    voltage_str = tags.get("voltage", "")
    
    # Parse voltage
    voltage_v: Optional[int] = None
    if voltage_str:
        try:
            # Handle formats like "380000", "380 kV", "380kV"
            clean_v = voltage_str.lower().replace("kv", "000").replace(" ", "").replace(",", "")
            voltage_v = int(clean_v)
        except ValueError:
            pass
    
    voltage_kv: Optional[float] = voltage_v / 1000 if voltage_v else None
    
    # Compute voltage_class and crossing_cost
    voltage_class = ""
    crossing_cost = ""
    if voltage_v:
        if voltage_v < 1000:
            voltage_class = "low"
            crossing_cost = "low"
        elif voltage_v < 50000:
            voltage_class = "medium"
            crossing_cost = "medium"
        elif voltage_v < 200000:
            voltage_class = "high"
            crossing_cost = "high"
        else:
            voltage_class = "extra_high"
            crossing_cost = "very_high"
    
    return {
        "osm_id": osm_id,
        "name": tags.get("name", ""),
        "power": power_type,
        "voltage": voltage_str,
        "voltage_v": voltage_v,
        "voltage_kv": voltage_kv,
        "voltage_class": voltage_class,
        "cables": tags.get("cables", ""),
        "operator": tags.get("operator", ""),
        "frequency": tags.get("frequency", ""),
        "ref": tags.get("ref", ""),
        "crossing_cost": crossing_cost,
        "location": tags.get("location", ""),
    }


def _build_pipelines_properties(osm_id: int, tags: Dict[str, str]) -> Dict[str, Any]:
    """Build pipelines properties with proper schema."""
    return {
        "osm_id": osm_id,
        "name": tags.get("name", ""),
        "man_made": tags.get("man_made", ""),
        "substance": tags.get("substance", ""),
        "operator": tags.get("operator", ""),
        "diameter": tags.get("diameter", ""),
        "location": tags.get("location", ""),
        "pressure": tags.get("pressure", ""),
    }

