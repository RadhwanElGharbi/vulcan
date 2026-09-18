import csv
import re
import math
import subprocess
import json
import sys
import shutil
import tempfile
import zipfile
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from shapely.geometry import shape, Point
from shapely.ops import unary_union
from pyproj import Geod
import reverse_geocoder as rg
import pycountry
import fiona
from .project_utils import discover_project_paths, resolve_project_path, load_json_file
router = APIRouter()
DOCS_ROOT = Path(__file__).resolve().parents[3] / 'docs'

def _first_existing_path(*candidates: Path) -> Path:
    """
    Return the first existing path from candidates (or the first candidate if none exist).

    This keeps compatibility across legacy doc layouts (`Project Instructions`, `Research`)
    and the newer normalized layout (`datasets`, `research`, `standards`).
    """
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return candidates[0]
RESEARCH_ROOT = _first_existing_path(DOCS_ROOT / 'research', DOCS_ROOT / 'Research')
ISO_CODES_CSV = _first_existing_path(RESEARCH_ROOT / 'iso_countries.csv', DOCS_ROOT / 'research' / 'iso_countries.csv', DOCS_ROOT / 'Research' / 'iso_countries.csv')
COUNTRY_COVERAGE_LONG_CSV = _first_existing_path(RESEARCH_ROOT / 'COUNTRY_COVERAGE_LONG.csv', DOCS_ROOT / 'research' / 'COUNTRY_COVERAGE_LONG.csv', DOCS_ROOT / 'Research' / 'COUNTRY_COVERAGE_LONG.csv')
COUNTRY_DATASETS_DIR = _first_existing_path(RESEARCH_ROOT / 'Country Coverage' / 'Country Datasets', DOCS_ROOT / 'research' / 'Country Coverage' / 'Country Datasets', DOCS_ROOT / 'Research' / 'Country Coverage' / 'Country Datasets')
DATASET_FETCH_PROTOCOL = str(_first_existing_path(DOCS_ROOT / 'datasets' / 'DATASET_FETCHING_PROTOCOLS.md', DOCS_ROOT / 'Project Instructions' / 'DATASET_FETCHING_PROTOCOLS.md'))
DATASET_COVERAGE_CATALOG_CSV = _first_existing_path(DOCS_ROOT / 'datasets' / 'WORLD_DATASET_CATALOGUE.csv', DOCS_ROOT / 'Project Instructions' / 'WORLD_DATASET_CATALOGUE.csv')
BOUNDARIES_ROOT = Path(__file__).resolve().parents[3] / '.runtime/boundaries'
NATURAL_EARTH_ADMIN0_URL = 'https://naturalearth.s3.amazonaws.com/50m_cultural/ne_50m_admin_0_countries.zip'
NATURAL_EARTH_ADMIN0_DIR = BOUNDARIES_ROOT / 'naturalearth_admin0_50m'
NATURAL_EARTH_ADMIN0_SHP = NATURAL_EARTH_ADMIN0_DIR / 'ne_50m_admin_0_countries.shp'
GADM_DIR = BOUNDARIES_ROOT / 'gadm41'
GEOD = Geod(ellps='WGS84')
EEA39_ISO3 = {'AUT', 'BEL', 'BGR', 'HRV', 'CYP', 'CZE', 'DNK', 'EST', 'FIN', 'FRA', 'DEU', 'GRC', 'HUN', 'IRL', 'ITA', 'LVA', 'LTU', 'LUX', 'MLT', 'NLD', 'POL', 'PRT', 'ROU', 'SVK', 'SVN', 'ESP', 'SWE', 'ISL', 'LIE', 'NOR', 'CHE', 'TUR', 'ALB', 'BIH', 'MNE', 'MKD', 'SRB', 'XKX', 'GBR'}

def _download_url_to_path(url: str, dest: Path, timeout_s: int=120) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.tmp')
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            with tmp.open('wb') as out:
                shutil.copyfileobj(resp, out)
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

def _ensure_naturalearth_admin0() -> Optional[Path]:
    """
    Ensure Natural Earth Admin0 boundaries exist on disk and return the .shp path.
    Falls back to None if download/extract fails.
    """
    try:
        if NATURAL_EARTH_ADMIN0_SHP.exists():
            return NATURAL_EARTH_ADMIN0_SHP
        NATURAL_EARTH_ADMIN0_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = NATURAL_EARTH_ADMIN0_DIR / 'ne_50m_admin_0_countries.zip'
        if not zip_path.exists():
            _download_url_to_path(NATURAL_EARTH_ADMIN0_URL, zip_path)
        if not zip_path.exists():
            return None
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(NATURAL_EARTH_ADMIN0_DIR)
        return NATURAL_EARTH_ADMIN0_SHP if NATURAL_EARTH_ADMIN0_SHP.exists() else None
    except Exception:
        return None

def _iso3_from_naturalearth_props(props: Dict[str, Any]) -> Optional[str]:
    candidates = [props.get('ISO_A3_EH'), props.get('ISO_A3'), props.get('ADM0_A3'), props.get('WB_A3'), props.get('SOV_A3')]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        val = candidate.strip().upper()
        if len(val) == 3 and val.isalpha() and (val != '-99'):
            return val
    return None

@lru_cache(maxsize=1)
def _load_naturalearth_admin0_features() -> List[Tuple[str, Tuple[float, float, float, float], Any]]:
    """
    Returns list of (iso3, bounds, shapely_geom) for world Admin0.
    Cached in-process for fast AOI intersection.
    """
    shp = _ensure_naturalearth_admin0()
    if not shp:
        return []
    out: List[Tuple[str, Tuple[float, float, float, float], Any]] = []
    with fiona.open(str(shp)) as src:
        for feat in src:
            geom = feat.get('geometry')
            if not geom:
                continue
            props = feat.get('properties') or {}
            iso3 = _iso3_from_naturalearth_props(props)
            if not iso3:
                continue
            try:
                sgeom = shape(geom)
            except Exception:
                continue
            out.append((iso3, sgeom.bounds, sgeom))
    return out

def _bbox_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or (a[1] > b[3]))

def _aoi_countries_admin0(geom: Any) -> List[str]:
    """
    Return ISO3 country codes intersecting the AOI polygon, using Natural Earth Admin0.
    """
    aoi_bounds = geom.bounds
    hits: List[str] = []
    seen: set[str] = set()
    for iso3, bounds, country_geom in _load_naturalearth_admin0_features():
        if iso3 in seen:
            continue
        if not _bbox_intersects(aoi_bounds, bounds):
            continue
        try:
            if country_geom.intersects(geom):
                seen.add(iso3)
                hits.append(iso3)
        except Exception:
            continue
    return hits

def _ensure_gadm_level(country_iso3: str, level: int) -> Optional[Path]:
    """
    Ensure a GADM level GeoPackage exists locally and return its path.
    Uses GADM 4.1 gpkg downloads and extracts a single level for performance.
    """
    iso3 = (country_iso3 or '').strip().upper()
    if len(iso3) != 3:
        return None
    GADM_DIR.mkdir(parents=True, exist_ok=True)
    extracted = GADM_DIR / f'gadm41_{iso3}_adm{level}.gpkg'
    if extracted.exists():
        return extracted
    full = GADM_DIR / f'gadm41_{iso3}.gpkg'
    if not full.exists():
        url = f'https://geodata.ucdavis.edu/gadm/gadm4.1/gpkg/gadm41_{iso3}.gpkg'
        _download_url_to_path(url, full, timeout_s=300)
        if not full.exists():
            return None
    try:
        layers = list(fiona.listlayers(str(full)))
    except Exception:
        layers = []
    preferred = f'ADM_ADM_{level}'
    layer = preferred if preferred in layers else None
    if not layer:
        suffix = f'_{level}'
        for cand in layers:
            if isinstance(cand, str) and cand.endswith(suffix):
                layer = cand
                break
    if not layer and layers:
        layer = layers[0]
    if not layer:
        return None
    temp_out = extracted.with_suffix('.gpkg.tmp')
    try:
        cmd = ['ogr2ogr', '-f', 'GPKG', str(temp_out), str(full), layer]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not temp_out.exists():
            if temp_out.exists():
                temp_out.unlink(missing_ok=True)
            return None
        if extracted.exists():
            extracted.unlink(missing_ok=True)
        temp_out.replace(extracted)
        return extracted
    except Exception:
        try:
            if temp_out.exists():
                temp_out.unlink()
        except OSError:
            pass
        return None

def _admin1_from_gadm_props(props: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (admin1_name, admin1_code) best-effort from a GADM feature properties.
    """
    name_candidates = [props.get('NAME_1'), props.get('NAME'), props.get('VARNAME_1'), props.get('NAME_EN')]
    code_candidates = [props.get('ISO_1'), props.get('HASC_1'), props.get('GID_1')]
    name = next((str(v).strip() for v in name_candidates if isinstance(v, str) and v.strip()), None)
    code = next((str(v).strip() for v in code_candidates if isinstance(v, str) and v.strip()), None)
    return (name, code)

def _aoi_admin1_for_country(aoi_geom: Any, iso3: str) -> List[Dict[str, Optional[str]]]:
    """
    Return Admin1 units intersecting the AOI for a given ISO3 country.
    """
    path = _ensure_gadm_level(iso3, level=1)
    if not path:
        return []
    try:
        layers = list(fiona.listlayers(str(path)))
        layer = layers[0] if layers else None
    except Exception:
        layer = None
    if not layer:
        return []
    aoi_bounds = aoi_geom.bounds
    out: List[Dict[str, Optional[str]]] = []
    seen: set[str] = set()
    with fiona.open(str(path), layer=layer) as src:
        for feat in src:
            geom = feat.get('geometry')
            if not geom:
                continue
            try:
                fgeom = shape(geom)
            except Exception:
                continue
            if not _bbox_intersects(aoi_bounds, fgeom.bounds):
                continue
            try:
                if not fgeom.intersects(aoi_geom):
                    continue
            except Exception:
                continue
            props = feat.get('properties') or {}
            name, code = _admin1_from_gadm_props(props)
            key = (code or name or '').strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({'iso3': iso3, 'admin1_name': name, 'admin1_code': code})
    return out

def compute_aoi_jurisdictions(aoi_feature_collection: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute AOI intersecting jurisdictions:
    - countries (Admin0): ISO3 list
    - admin1: list of { iso3, admin1_name, admin1_code }
    """
    geom = _collect_geometry(aoi_feature_collection)
    countries = _aoi_countries_admin0(geom)
    admin1: List[Dict[str, Optional[str]]] = []
    for iso3 in countries:
        admin1.extend(_aoi_admin1_for_country(geom, iso3))
    return {'countries_iso3': countries, 'admin1': admin1}

def _global_row_applicable_to_project(row: Dict[str, str], project_iso3: str) -> bool:
    dataset = _sanitize_str(row.get('Dataset')) or ''
    if dataset.lower() == 'copernicus dem eea-10':
        return project_iso3 in EEA39_ISO3
    return True

class ProjectMetadata(BaseModel):
    """Project metadata model"""
    project_name: str
    project_id: Optional[str] = None
    project_code: Optional[str] = None
    client: Optional[str] = None
    date_created: Optional[str] = None
    status: Optional[str] = None
    crs: Optional[Dict[str, Any]] = None
    aoi: Optional[Dict[str, Any]] = None
    measurement_system: Optional[str] = None
    units: Optional[Dict[str, str]] = None
    country: Optional[str] = None
    iso3: Optional[str] = None
    iso3_list: Optional[List[str]] = None
    countries: Optional[List[Dict[str, str]]] = None
    organization: Optional[str] = None
    department: Optional[str] = None
    project_creator: Optional[str] = None
    project_type: Optional[str] = None
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    folder_color: Optional[str] = None

class DatasetInfo(BaseModel):
    """Dataset information model"""
    name: str
    type: str
    path: str
    metadata: Optional[Dict[str, Any]] = None

class ProjectDatasets(BaseModel):
    """Project datasets model"""
    rasters: List[DatasetInfo]
    vectors: List[DatasetInfo]
    multidimensional: List[DatasetInfo] = []
    tables: List[DatasetInfo] = []

class DatasetCoverageEntry(BaseModel):
    dataset: str
    source: Optional[str] = None
    data_type: Optional[str] = None
    access: Optional[str] = None
    coverage: Optional[str] = None
    temporal_start: Optional[str] = None
    temporal_end: Optional[str] = None
    frequency: Optional[str] = None
    applies_globally: bool = False
    url: Optional[str] = None

class DatasetCoverageResponse(BaseModel):
    iso3: str
    country: Optional[str]
    entries: List[DatasetCoverageEntry]
    summary: Optional[str] = None
    protocol_reference: str

class ProjectCRSRecommendation(BaseModel):
    epsg: int
    name: str
    reason: str
    utm_zone: Optional[int] = None
    hemisphere: Optional[str] = None

class AOIPreviewResponse(BaseModel):
    area_km2: float
    countries: List[str]
    iso3: Optional[str] = None
    country: Optional[str] = None
    centroid: Dict[str, float]
    recommended_crs: ProjectCRSRecommendation
    start_point_within: Optional[bool] = None
    end_point_within: Optional[bool] = None

def _sanitize_project_name(name: str) -> str:
    sanitized = re.sub('[^A-Za-z0-9-]+', '-', name).strip('-')
    if not sanitized:
        raise HTTPException(status_code=400, detail='Project name must contain letters, numbers, or hyphens.')
    return sanitized

def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=4)

@lru_cache(maxsize=512)
def _gdalinfo_json_cached(path_str: str, mtime_ns: int, include_stats: bool) -> Optional[Dict[str, Any]]:
    """
    Probe raster metadata with process-level memoization.

    Keyed by full path + mtime so repeat requests for unchanged rasters avoid
    expensive subprocess calls. This significantly reduces latency for
    /projects/{name}/datasets on large projects.
    """
    try:
        cmd = ['gdalinfo', '-json']
        if include_stats:
            cmd.append('-stats')
        cmd.append(path_str)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30 if include_stats else 12)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None

def _gdalinfo_json(path: Path, include_stats: bool=False) -> Optional[Dict[str, Any]]:
    """
    Probe raster metadata using GDAL with mtime-aware memoization.
    """
    try:
        mtime_ns = int(path.stat().st_mtime_ns)
    except Exception:
        return None
    return _gdalinfo_json_cached(str(path), mtime_ns, include_stats)

def _raster_metadata_needs_probe(metadata: Dict[str, Any]) -> bool:
    """
    Return True when metadata is too sparse for map/data consumers.
    """
    if not isinstance(metadata, dict) or not metadata:
        return True
    required_keys = ('dataset_name', 'data_type', 'format', 'probed_crs', 'extent', 'bbox_wgs84', 'pixel_data_type', 'dimensions')
    for key in required_keys:
        value = metadata.get(key)
        if value in (None, '', [], {}):
            return True
    return False

def _extract_epsg_from_gdalinfo(info: Dict[str, Any]) -> Optional[str]:
    cs = info.get('coordinateSystem') or {}
    wkt = cs.get('wkt') if isinstance(cs, dict) else None
    if not isinstance(wkt, str) or not wkt:
        return None
    matches = re.findall('ID\\["EPSG",\\s*(\\d+)\\]', wkt)
    if not matches:
        return None
    crs_codes = [int(m) for m in matches if not 8000 <= int(m) < 10000]
    if crs_codes:
        return f'EPSG:{crs_codes[-1]}'
    return f'EPSG:{matches[-1]}'

def _collect_bounds_from_geojson(geom: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    bounds = [float('inf'), float('inf'), float('-inf'), float('-inf')]

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
        geom_type = obj.get('type') if isinstance(obj, dict) else None
        if geom_type == 'FeatureCollection':
            for feature in obj.get('features', []):
                _collect(feature.get('geometry'))
        elif geom_type == 'Feature':
            _collect(obj.get('geometry'))
        elif geom_type == 'GeometryCollection':
            for g in obj.get('geometries', []):
                _collect(g)
        elif isinstance(obj, dict):
            _walk(obj.get('coordinates'))
    _collect(geom)
    if float('inf') in bounds or float('-inf') in bounds or bounds[0] == bounds[2] or (bounds[1] == bounds[3]):
        return None
    return (bounds[0], bounds[1], bounds[2], bounds[3])

def _bbox_from_wgs84_extent(extent_geom: Dict[str, Any]) -> Optional[Dict[str, float]]:
    bounds = _collect_bounds_from_geojson(extent_geom)
    if not bounds:
        return None
    minx, miny, maxx, maxy = bounds
    return {'west': minx, 'south': miny, 'east': maxx, 'north': maxy, 'crs': 'EPSG:4326'}

def _extent_from_gdalinfo(info: Dict[str, Any], crs_override: Optional[str]=None) -> Optional[Dict[str, float]]:
    corners = info.get('cornerCoordinates')
    if not isinstance(corners, dict):
        return None
    xs: List[float] = []
    ys: List[float] = []
    for point in corners.values():
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    if not xs or not ys:
        return None
    native_crs = crs_override or _extract_epsg_from_gdalinfo(info) or 'unknown'
    return {'minx': min(xs), 'miny': min(ys), 'maxx': max(xs), 'maxy': max(ys), 'crs': native_crs}

def _gdal_type_bit_depth(gdal_type: Optional[str]) -> Optional[int]:
    if not gdal_type:
        return None
    t = str(gdal_type).strip()
    if not t:
        return None
    if t.lower() == 'byte':
        return 8
    m = re.search('(\\d+)', t)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None

def _gdal_type_pixel_kind(gdal_type: Optional[str]) -> Optional[str]:
    if not gdal_type:
        return None
    t = str(gdal_type).strip().lower()
    if not t:
        return None
    if t.startswith(('cfloat', 'complex')):
        return 'complex'
    if t.startswith('float'):
        return 'floating_point'
    if t.startswith(('int', 'uint')) or t == 'byte':
        return 'integer'
    return None

def _extract_wgs84_center_lat(info: Dict[str, Any]) -> Optional[float]:
    wgs84 = info.get('wgs84Extent')
    if not isinstance(wgs84, dict):
        return None
    bbox = _bbox_from_wgs84_extent(wgs84)
    if not bbox:
        return None
    try:
        return (float(bbox['north']) + float(bbox['south'])) / 2.0
    except (TypeError, ValueError, KeyError):
        return None

def _extract_raster_statistics_from_gdalinfo(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    bands = info.get('bands')
    if not isinstance(bands, list) or not bands:
        return None

    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    out: Dict[str, Any] = {'min': None, 'max': None, 'mean': None, 'stddev': None}
    band0 = bands[0] if isinstance(bands[0], dict) else {}
    out['min'] = _safe_float(band0.get('minimum'))
    out['max'] = _safe_float(band0.get('maximum'))
    out['mean'] = _safe_float(band0.get('mean'))
    out['stddev'] = _safe_float(band0.get('stdDev'))
    stats_meta = (band0.get('metadata') or {}).get('', {})
    if isinstance(stats_meta, dict):
        if out['min'] is None:
            out['min'] = _safe_float(stats_meta.get('STATISTICS_MINIMUM'))
        if out['max'] is None:
            out['max'] = _safe_float(stats_meta.get('STATISTICS_MAXIMUM'))
        if out['mean'] is None:
            out['mean'] = _safe_float(stats_meta.get('STATISTICS_MEAN'))
        if out['stddev'] is None:
            out['stddev'] = _safe_float(stats_meta.get('STATISTICS_STDDEV'))
        valid_pct = _safe_float(stats_meta.get('STATISTICS_VALID_PERCENT'))
        if valid_pct is not None:
            out['valid_percent'] = valid_pct
    if any((v is not None for v in out.values())):
        return {k: v for k, v in out.items() if v is not None}
    return None

def _extract_raster_technical_fields_from_gdalinfo(info: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if info.get('driverShortName'):
        out['driver'] = str(info['driverShortName'])
    if info.get('driverLongName'):
        out['driver_long_name'] = str(info['driverLongName'])
    size = info.get('size')
    width: Optional[int] = None
    height: Optional[int] = None
    if isinstance(size, list) and len(size) >= 2:
        try:
            width = int(size[0])
            height = int(size[1])
            out['dimensions'] = {'width': width, 'height': height}
        except (TypeError, ValueError):
            width = None
            height = None
    geo = info.get('geoTransform')
    if isinstance(geo, list) and len(geo) >= 6:
        out['geotransform'] = geo
        try:
            cell_x = abs(float(geo[1]))
            cell_y = abs(float(geo[5]))
            out['cell_size'] = {'x': cell_x, 'y': cell_y}
        except (TypeError, ValueError):
            pass
    bands = info.get('bands')
    if isinstance(bands, list):
        out['band_count'] = len(bands)
        if bands:
            b0 = bands[0] if isinstance(bands[0], dict) else {}
            nodata = b0.get('noDataValue')
            if nodata is not None:
                out['nodata_value'] = nodata
            gdal_type = b0.get('type')
            if gdal_type:
                out['pixel_data_type'] = gdal_type
                bits = _gdal_type_bit_depth(str(gdal_type))
                if bits is not None:
                    out['pixel_depth_bits'] = bits
                    if width and height:
                        try:
                            out['uncompressed_size_bytes'] = int(width) * int(height) * max(1, len(bands)) * ((bits + 7) // 8)
                        except Exception:
                            pass
                kind = _gdal_type_pixel_kind(str(gdal_type))
                if kind:
                    out['pixel_type'] = kind
            block = b0.get('block')
            if isinstance(block, list) and len(block) >= 2:
                out['block_size'] = {'x': block[0], 'y': block[1]}
            if b0.get('colorInterpretation'):
                out['color_interpretation'] = b0.get('colorInterpretation')
            out['has_colormap'] = 'colorTable' in b0
            ovs = b0.get('overviews')
            if isinstance(ovs, list):
                out['pyramid_levels'] = len(ovs)
                out['overviews'] = ovs
            if b0.get('unitType'):
                out['unit_type'] = b0.get('unitType')
    md = info.get('metadata') or {}
    if isinstance(md, dict):
        img_struct = md.get('IMAGE_STRUCTURE') or {}
        if isinstance(img_struct, dict):
            if img_struct.get('COMPRESSION'):
                out['compression'] = img_struct.get('COMPRESSION')
            if img_struct.get('INTERLEAVE'):
                out['interleave'] = img_struct.get('INTERLEAVE')
    if isinstance(geo, list) and len(geo) >= 6:
        try:
            cell_x_native = abs(float(geo[1]))
            cell_y_native = abs(float(geo[5]))
        except (TypeError, ValueError):
            cell_x_native = None
            cell_y_native = None
        cs = info.get('coordinateSystem') or {}
        wkt = cs.get('wkt') if isinstance(cs, dict) else None
        if isinstance(wkt, str) and cell_x_native and cell_y_native:
            wkt_top = wkt.lstrip().upper()
            is_geographic = wkt_top.startswith('GEOGCRS[') or wkt_top.startswith('GEOGCS[')
            if is_geographic:
                out['cell_size_units'] = 'degree'
                lat = _extract_wgs84_center_lat(info)
                if lat is not None:
                    lat_rad = math.radians(float(lat))
                    x_m = cell_x_native * 111320.0 * max(0.0, math.cos(lat_rad))
                    y_m = cell_y_native * 111320.0
                    out['resolution_x_m'] = x_m
                    out['resolution_y_m'] = y_m
                    out['resolution_m'] = (x_m + y_m) / 2.0
            else:
                matches = re.findall('LENGTHUNIT\\["([^"]+)",\\s*([-0-9.eE\\+]+)\\]', wkt)
                unit_name = None
                unit_to_m = None
                if matches:
                    unit_name = matches[-1][0]
                    try:
                        unit_to_m = float(matches[-1][1])
                    except ValueError:
                        unit_to_m = None
                if unit_name:
                    out['cell_size_units'] = unit_name
                if unit_to_m is not None:
                    x_m = cell_x_native * unit_to_m
                    y_m = cell_y_native * unit_to_m
                    out['resolution_x_m'] = x_m
                    out['resolution_y_m'] = y_m
                    out['resolution_m'] = (x_m + y_m) / 2.0
    return out

def _save_upload_to_temp(upload: UploadFile, temp_dir: Path) -> Path:
    destination = temp_dir / upload.filename
    with destination.open('wb') as buffer:
        shutil.copyfileobj(upload.file, buffer)
    upload.file.seek(0)
    return destination

def _convert_vector_to_geojson(source_path: Path, temp_dir: Path) -> Dict[str, Any]:
    if source_path.suffix.lower() in ['.json', '.geojson']:
        with source_path.open('r', encoding='utf-8') as fh:
            return json.load(fh)
    output = temp_dir / 'converted.geojson'
    cmd = ['ogr2ogr', '-f', 'GeoJSON', str(output), str(source_path), '-t_srs', 'EPSG:4326']
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=f'Failed to convert AOI: {result.stderr}')
    with output.open('r', encoding='utf-8') as fh:
        return json.load(fh)

def _collect_geometry(feature_collection: Dict[str, Any]):
    features = feature_collection.get('features') or []
    geometries = []
    for feature in features:
        geometry = feature.get('geometry')
        if not geometry:
            continue
        try:
            parsed = shape(geometry)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise HTTPException(status_code=400, detail='AOI geometry is malformed.') from exc
        if parsed.is_empty or not parsed.is_valid or parsed.geom_type not in ('Polygon', 'MultiPolygon'):
            raise HTTPException(status_code=400, detail='AOI must contain valid, nonempty polygons.')
        geometries.append(parsed)
    if not geometries:
        raise HTTPException(status_code=400, detail='No geometry found in AOI.')
    if len(geometries) == 1:
        return geometries[0]
    return unary_union(geometries)

def _calculate_area_km2(feature_collection: Dict[str, Any]) -> float:
    geom = _collect_geometry(feature_collection)
    area, _ = GEOD.geometry_area_perimeter(geom)
    return round(abs(area) / 1000000, 2)

def _infer_country_from_point(lat: float, lon: float) -> Tuple[Optional[str], Optional[str]]:
    try:
        results = rg.search([(lat, lon)], mode=1)
    except Exception:
        return (None, None)
    if not results:
        return (None, None)
    iso2 = results[0].get('cc')
    if not iso2:
        return (None, None)
    try:
        country = pycountry.countries.get(alpha_2=iso2.upper())
        if not country:
            return (None, None)
        return (country.alpha_3, country.name)
    except Exception:
        return (None, None)

def _generate_project_id(organization: str, project_name: str, iso3: Optional[str]) -> str:
    org = re.sub('[^A-Za-z0-9]+', '', organization.upper()) or 'ORG'
    name = re.sub('[^A-Za-z0-9]+', '_', project_name).strip('_') or 'PROJECT'
    iso = (iso3 or 'UNK').upper()
    year = datetime.utcnow().year
    prefix = f'{org}_{name}_{iso}_{year}_'
    existing = discover_project_paths()
    seq = 1
    for project_dir in existing.values():
        metadata_path = project_dir / 'project_metadata.json'
        metadata = load_json_file(metadata_path) if metadata_path.exists() else None
        project_id = metadata.get('project_id') if metadata else None
        if project_id and project_id.startswith(prefix):
            try:
                suffix = int(project_id.split('_')[-1])
                seq = max(seq, suffix + 1)
            except ValueError:
                continue
    return f'{prefix}{seq:03d}'

def _load_geojson_string(payload: str) -> Dict[str, Any]:
    try:
        data = json.loads(payload)
        if data.get('type') != 'FeatureCollection':
            raise ValueError('Expected FeatureCollection')
        return data
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'Invalid GeoJSON payload: {exc}') from exc

def _prepare_aoi_payload(aoi_file: Optional[UploadFile], drawn_geojson: Optional[str], temp_dir: Path) -> Dict[str, Any]:
    if aoi_file:
        saved = _save_upload_to_temp(aoi_file, temp_dir)
        return _convert_vector_to_geojson(saved, temp_dir)
    if drawn_geojson:
        return _load_geojson_string(drawn_geojson)
    raise HTTPException(status_code=400, detail='AOI geometry is required.')

@router.post('/projects/aoi/preview', response_model=AOIPreviewResponse)
async def preview_aoi(aoi_file: Optional[UploadFile]=File(None), drawn_geojson: Optional[str]=Form(None), start_point_lat: Optional[float]=Form(None), start_point_lon: Optional[float]=Form(None), end_point_lat: Optional[float]=Form(None), end_point_lon: Optional[float]=Form(None)):
    """
    Analyze AOI geometry to provide area, centroid, inferred countries, and recommended CRS.
    Optionally checks if start/end points are within the AOI.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        feature_collection = _prepare_aoi_payload(aoi_file, drawn_geojson, temp_dir)
        geom = _collect_geometry(feature_collection)
        area_km2 = _calculate_area_km2(feature_collection)
        centroid = geom.centroid
        iso3, country_name = _infer_country_from_point(centroid.y, centroid.x)
        zone, hemi, epsg = _calculate_utm_zone(centroid.x, centroid.y)
        crs_name = f'WGS 84 / UTM zone {zone}{hemi}'
        start_point_within = None
        end_point_within = None
        if start_point_lat is not None and start_point_lon is not None:
            start_pt = Point(start_point_lon, start_point_lat)
            start_point_within = geom.contains(start_pt) or geom.touches(start_pt)
        if end_point_lat is not None and end_point_lon is not None:
            end_pt = Point(end_point_lon, end_point_lat)
            end_point_within = geom.contains(end_pt) or geom.touches(end_pt)
        return AOIPreviewResponse(area_km2=area_km2, countries=[country_name] if country_name else [], iso3=iso3, country=country_name, centroid={'lat': centroid.y, 'lon': centroid.x}, recommended_crs=ProjectCRSRecommendation(epsg=epsg, name=crs_name, reason=f'AOI centroid at ({centroid.y:.4f}, {centroid.x:.4f})', utm_zone=zone, hemisphere=hemi), start_point_within=start_point_within, end_point_within=end_point_within)

@router.get('/projects', response_model=List[ProjectMetadata])
async def list_projects():
    projects = []
    for name, directory in sorted(discover_project_paths(force_refresh=True).items()):
        metadata = load_json_file(directory / 'project_metadata.json') or {'project_name': name}
        projects.append(ProjectMetadata(**_normalize_project_metadata(metadata, directory)))
    return projects

def _normalize_project_metadata(raw: Dict[str, Any], project_path: Optional[Path]=None) -> Dict[str, Any]:
    """
    Normalize project metadata to ensure consistent structure.
    Converts flat crs_epsg/crs_name fields to nested crs object.
    Merges AOI data from project_aoi.json if available.
    """
    result = dict(raw)
    if 'crs' not in result or result['crs'] is None:
        crs_obj = {}
        if 'crs_epsg' in result:
            crs_obj['epsg'] = result.pop('crs_epsg')
        if 'crs_name' in result:
            crs_obj['name'] = result.pop('crs_name')
        if 'crs_proj4' in result:
            crs_obj['proj4'] = result.pop('crs_proj4')
        if 'crs_units' in result:
            crs_obj['units'] = result.pop('crs_units')
        if crs_obj:
            result['crs'] = crs_obj
    if 'aoi' not in result or result['aoi'] is None:
        aoi_obj = {}
        if 'aoi_file' in result:
            aoi_obj['file'] = result.pop('aoi_file')
        if 'aoi_area_km2' in result:
            aoi_obj['area_km2'] = result.pop('aoi_area_km2')
        if 'aoi_countries' in result:
            aoi_obj['countries'] = result.pop('aoi_countries')
        if project_path:
            aoi_json_path = project_path / 'aoi' / 'project_aoi.json'
            if aoi_json_path.exists():
                aoi_data = load_json_file(aoi_json_path)
                if aoi_data:
                    if 'file' not in aoi_obj and 'aoi_file' in aoi_data:
                        aoi_obj['file'] = aoi_data['aoi_file']
                    if 'area_km2' not in aoi_obj:
                        if 'aoi_area_km2' in aoi_data:
                            aoi_obj['area_km2'] = aoi_data['aoi_area_km2']
                        else:
                            aoi_gpkg = project_path / 'aoi' / 'aoi.gpkg'
                            if aoi_gpkg.exists():
                                aoi_obj['file'] = str(aoi_gpkg)
                                area = _calculate_aoi_area(aoi_gpkg)
                                if area:
                                    aoi_obj['area_km2'] = area
                    if 'countries' not in aoi_obj:
                        if 'iso3_list' in result and isinstance(result['iso3_list'], list) and result['iso3_list']:
                            _iso_map, _, _ = _load_iso_mappings()
                            aoi_obj['countries'] = [_iso_map.get(c, c) for c in result['iso3_list']]
                        elif 'countries' in result and isinstance(result['countries'], list) and result['countries']:
                            aoi_obj['countries'] = [c.get('name', c.get('iso3', '')) if isinstance(c, dict) else str(c) for c in result['countries']]
                        elif 'aoi_countries' in aoi_data:
                            aoi_obj['countries'] = aoi_data['aoi_countries']
                        elif 'country' in result:
                            aoi_obj['countries'] = [result['country']]
                        elif 'iso3' in result:
                            _iso_map, _, _ = _load_iso_mappings()
                            country_name = _iso_map.get(result['iso3'])
                            if country_name:
                                aoi_obj['countries'] = [country_name]
                    if 'start_point' not in aoi_obj and 'start_point' in aoi_data:
                        sp = aoi_data['start_point']
                        aoi_obj['start_point'] = {'latitude': sp.get('latitude'), 'longitude': sp.get('longitude')}
                    if 'end_point' not in aoi_obj and 'end_point' in aoi_data:
                        ep = aoi_data['end_point']
                        aoi_obj['end_point'] = {'latitude': ep.get('latitude'), 'longitude': ep.get('longitude')}
        if aoi_obj:
            result['aoi'] = aoi_obj
    return result

def _calculate_aoi_area(aoi_path: Path) -> Optional[float]:
    """
    Calculate AOI area in km² using ogr2ogr and basic geometry calculation.
    """
    try:
        cmd = ['ogrinfo', '-ro', '-al', '-geom=YES', str(aoi_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return None
        for line in result.stdout.split('\n'):
            if 'Extent:' in line:
                import re
                match = re.search('Extent:\\s*\\(([-\\d.]+),\\s*([-\\d.]+)\\)\\s*-\\s*\\(([-\\d.]+),\\s*([-\\d.]+)\\)', line)
                if match:
                    minx, miny, maxx, maxy = map(float, match.groups())
                    width_m = abs(maxx - minx)
                    height_m = abs(maxy - miny)
                    area_m2 = width_m * height_m
                    area_km2 = area_m2 / 1000000
                    return round(area_km2, 2)
        return None
    except Exception:
        return None

@router.get('/projects/{project_name}/metadata', response_model=ProjectMetadata)
async def get_project_metadata(project_name: str):
    """
    Get full metadata for a specific project
    """
    project_path = resolve_project_path(project_name)
    if not project_path or not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found (missing project_metadata.json)")
    metadata_file = project_path / 'project_metadata.json'
    metadata = load_json_file(metadata_file) if metadata_file.exists() else None
    if not metadata:
        raise HTTPException(status_code=500, detail=f"Failed to load metadata for '{project_name}'")
    normalized = _normalize_project_metadata(metadata, project_path)
    if not normalized.get('iso3_list'):
        try:
            if NATURAL_EARTH_ADMIN0_SHP.exists():
                aoi_fc = _load_project_aoi_feature_collection(project_path)
                if isinstance(aoi_fc, dict):
                    geom = _collect_geometry(aoi_fc)
                    detected = _aoi_countries_admin0(geom)
                    if detected:
                        iso_map, _, _ = _load_iso_mappings()
                        names = [iso_map.get(c, c) for c in detected]
                        normalized['iso3_list'] = detected
                        normalized['countries'] = [{'iso3': c, 'name': n} for c, n in zip(detected, names)]
                        normalized['country'] = ', '.join(names)
                        aoi = normalized.get('aoi')
                        if isinstance(aoi, dict):
                            aoi['countries'] = names
        except Exception:
            pass
    return ProjectMetadata(**normalized)

def _build_display_name_from_metadata(metadata: Optional[Dict[str, Any]], fallback_name: str) -> str:
    """
    Build display name from metadata JSON sidecar.
    Format: {category}_{dataset_name}_{target_crs}_processed
    Where dataset_name has spaces replaced with hyphens.
    target_crs is formatted as EPSGnumber (no colon).
    """
    if not isinstance(metadata, dict):
        return fallback_name
    category = metadata.get('category', '')
    dataset_name = metadata.get('dataset_name', '')
    target_crs = metadata.get('target_crs', '')
    if dataset_name.endswith(' (Processed)'):
        dataset_name = dataset_name[:-12]
    dataset_name = dataset_name.replace(' ', '-')
    target_crs = target_crs.replace(':', '')
    if category and dataset_name and target_crs:
        return f'{category}_{dataset_name}_{target_crs}_processed'
    return fallback_name

@router.get('/projects/{project_name}/datasets', response_model=ProjectDatasets)
async def list_project_datasets(project_name: str):
    """
    List all available datasets for a project
    
    Scans data/rasters/processed/ and data/vectors/processed/ directories directly.
    This is the canonical source - symlinks in parent folders are deprecated.
    Reads metadata from .json sidecars if available.
    
    Display names follow the format: {category}_{dataset_name}_{target_crs}_processed
    where dataset_name has spaces replaced with hyphens.
    """
    project_path = resolve_project_path(project_name)
    if not project_path or not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found (missing project root with project_metadata.json)")
    rasters_processed_dir = project_path / 'data' / 'rasters' / 'processed'
    vectors_processed_dir = project_path / 'data' / 'vectors' / 'processed'
    rasters = []
    vectors = []
    if rasters_processed_dir.exists():
        for item in rasters_processed_dir.iterdir():
            if item.suffix == '.tif' and (not item.name.endswith('.json')):
                metadata_file = item.with_name(f'{item.name}.json')
                metadata: Dict[str, Any] = {}
                if metadata_file.exists():
                    loaded = load_json_file(metadata_file)
                    if isinstance(loaded, dict):
                        metadata = loaded
                import re
                raw_name = item.stem
                fallback_name = re.sub('_epsg\\d+_processed$', '', raw_name, flags=re.IGNORECASE)
                fallback_name = re.sub('_processed$', '', fallback_name, flags=re.IGNORECASE)
                updated = False
                info = None
                if not metadata.get('dataset_name'):
                    metadata['dataset_name'] = fallback_name or raw_name
                    updated = True
                if metadata.get('data_type') in {None, '', 'Float32', 'Float64', 'Int16', 'Int32', 'UInt16', 'UInt32', 'Byte'}:
                    metadata['data_type'] = 'Raster'
                    updated = True
                if not metadata.get('format'):
                    metadata['format'] = 'GeoTIFF'
                    updated = True
                if _raster_metadata_needs_probe(metadata):
                    info = _gdalinfo_json(item, include_stats=False)
                if info:
                    epsg = _extract_epsg_from_gdalinfo(info)
                    if epsg and metadata.get('probed_crs') != epsg:
                        metadata['probed_crs'] = epsg
                        updated = True
                    tech = _extract_raster_technical_fields_from_gdalinfo(info)
                    for k, v in tech.items():
                        if metadata.get(k) != v:
                            metadata[k] = v
                            updated = True
                    extent = _extent_from_gdalinfo(info, crs_override=epsg)
                    if extent and metadata.get('extent') != extent:
                        metadata['extent'] = extent
                        updated = True
                    wgs84 = info.get('wgs84Extent')
                    if isinstance(wgs84, dict):
                        bbox = _bbox_from_wgs84_extent(wgs84)
                        if bbox and metadata.get('bbox_wgs84') != bbox:
                            metadata['bbox_wgs84'] = bbox
                            updated = True
                    stats = _extract_raster_statistics_from_gdalinfo(info)
                    if stats and metadata.get('statistics') != stats:
                        metadata['statistics'] = stats
                        updated = True
                if updated:
                    try:
                        _write_json(metadata_file, metadata)
                    except Exception:
                        pass
                display_name = _build_display_name_from_metadata(metadata, fallback_name)
                dataset_info = DatasetInfo(name=display_name, type='raster', path=str(item.relative_to(project_path)))
                if metadata:
                    dataset_info.metadata = metadata
                rasters.append(dataset_info)
    if vectors_processed_dir.exists():
        for item in vectors_processed_dir.iterdir():
            if item.suffix == '.gpkg' and (not item.name.endswith('.json')):
                metadata_file = item.with_name(f'{item.name}.json')
                metadata: Dict[str, Any] = {}
                if metadata_file.exists():
                    loaded = load_json_file(metadata_file)
                    if isinstance(loaded, dict):
                        metadata = loaded
                import re
                raw_name = item.stem
                fallback_name = re.sub('_epsg\\d+_processed$', '', raw_name, flags=re.IGNORECASE)
                fallback_name = re.sub('_processed$', '', fallback_name, flags=re.IGNORECASE)
                display_name = _build_display_name_from_metadata(metadata, fallback_name)
                dataset_info = DatasetInfo(name=display_name, type='vector', path=str(item.relative_to(project_path)))
                if metadata:
                    dataset_info.metadata = metadata
                vectors.append(dataset_info)
    from .dataset_fetch.research.readers import active_datasets, display_metadata
    for entry in rasters + vectors:
        entry.metadata = {**(entry.metadata or {}), 'provenance_status': 'legacy_unverified', 'validation_status': 'legacy_unverified'}
    multidimensional = []
    tables = []
    for item in active_datasets(project_path):
        if item['kind'] == 'table':
            tables.append(DatasetInfo(name=item['id'],type='table',path=item['path'],metadata=display_metadata(item,project_path.name)))
            continue
        if item['kind'] == 'climate':
            multidimensional.append(DatasetInfo(name=item['id'],type='climate',path=item['path'],metadata=display_metadata(item,project_path.name)))
            continue
        if item['kind'] not in ('raster', 'vector'):
            continue
        info = DatasetInfo(name=item['id'], type=item['kind'], path=item['path'], metadata=display_metadata(item, project_path.name))
        (rasters if item['kind'] == 'raster' else vectors).append(info)
    rasters.sort(key=lambda x: x.name.lower())
    vectors.sort(key=lambda x: x.name.lower())
    return ProjectDatasets(rasters=rasters, vectors=vectors, multidimensional=multidimensional,tables=tables)

class DatasetFingerprint(BaseModel):
    """Lightweight fingerprint for detecting dataset changes."""
    raster_count: int
    vector_count: int
    latest_modified: Optional[str] = None
    fingerprint: str

@router.get('/projects/{project_name}/datasets/fingerprint', response_model=DatasetFingerprint)
async def get_dataset_fingerprint(project_name: str):
    """
    Get a lightweight fingerprint of project datasets for change detection.

    This endpoint is designed for frequent polling (every 10 seconds) to detect
    when new datasets have been added without fetching full dataset details.

    Returns:
        - raster_count: Number of processed raster files
        - vector_count: Number of processed vector files
        - latest_modified: ISO timestamp of most recently modified file
        - fingerprint: MD5 hash of all filenames + modification times
    """
    import hashlib
    project_path = resolve_project_path(project_name)
    if not project_path or not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
    rasters_processed_dir = project_path / 'data' / 'rasters' / 'processed'
    vectors_processed_dir = project_path / 'data' / 'vectors' / 'processed'
    raster_count = 0
    vector_count = 0
    latest_mtime = 0.0
    fingerprint_parts = []
    from .dataset_fetch.research.readers import active_datasets
    for item in active_datasets(project_path):
        raster_count += int(item['kind'] == 'raster')
        vector_count += int(item['kind'] == 'vector')
        fingerprint_parts.append(item['id'] + ':' + item['sha256'])
    if rasters_processed_dir.exists():
        for item in rasters_processed_dir.iterdir():
            if item.suffix == '.tif' and (not item.name.endswith('.json')):
                raster_count += 1
                mtime = item.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                fingerprint_parts.append(f'{item.name}:{mtime}')
    if vectors_processed_dir.exists():
        for item in vectors_processed_dir.iterdir():
            if item.suffix == '.gpkg' and (not item.name.endswith('.json')):
                vector_count += 1
                mtime = item.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                fingerprint_parts.append(f'{item.name}:{mtime}')
    fingerprint_parts.sort()
    fingerprint_str = '|'.join(fingerprint_parts)
    fingerprint_hash = hashlib.md5(fingerprint_str.encode()).hexdigest()
    from datetime import datetime, timezone
    latest_modified = None
    if latest_mtime > 0:
        latest_modified = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()
    return DatasetFingerprint(raster_count=raster_count, vector_count=vector_count, latest_modified=latest_modified, fingerprint=fingerprint_hash)

def _sanitize_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    val = str(value).strip()
    return val or None

@lru_cache()
def _load_iso_mappings():
    iso_to_name: Dict[str, str] = {}
    name_to_iso: Dict[str, str] = {}
    alpha2_to_iso: Dict[str, str] = {}
    if not ISO_CODES_CSV.exists():
        return (iso_to_name, name_to_iso, alpha2_to_iso)
    with ISO_CODES_CSV.open('r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = _sanitize_str(row.get('name'))
            alpha3 = _sanitize_str(row.get('alpha-3'))
            alpha2 = _sanitize_str(row.get('alpha-2'))
            if not alpha3:
                continue
            iso_to_name[alpha3.upper()] = name
            if name:
                normalized = re.sub('[^a-z0-9]+', '', name.lower())
                name_to_iso[normalized] = alpha3.upper()
            if alpha2:
                alpha2_to_iso[alpha2.upper()] = alpha3.upper()
    return (iso_to_name, name_to_iso, alpha2_to_iso)

def _normalize_country_value(value: Optional[str]) -> Optional[str]:
    val = _sanitize_str(value)
    if not val:
        return None
    common_abbreviations = {'UAE': 'ARE', 'UK': 'GBR', 'USA': 'USA', 'US': 'USA', 'RUSSIA': 'RUS'}
    iso_to_name, name_to_iso, alpha2_to_iso = _load_iso_mappings()
    upper = val.upper()
    if upper in common_abbreviations:
        return common_abbreviations[upper]
    try:
        return pycountry.countries.lookup(val).alpha_3
    except LookupError:
        pass
    if len(upper) == 3 and upper.isalpha():
        if upper in iso_to_name:
            return upper
    if len(upper) == 2 and upper.isalpha():
        return alpha2_to_iso.get(upper)
    normalized = re.sub('[^a-z0-9]+', '', val.lower())
    return name_to_iso.get(normalized)

def _extract_iso_from_dict(data: Dict[str, Any]) -> Optional[str]:
    candidate_keys = ['country_code', 'country', 'country_iso', 'countryName', 'iso3', 'iso', 'alpha3', 'nation']
    for key in candidate_keys:
        if key in data:
            iso = _normalize_country_value(data.get(key))
            if iso:
                return iso
    return None

def _infer_project_iso3(project_path: Path) -> Optional[str]:
    metadata_path = project_path / 'project_metadata.json'
    metadata = load_json_file(metadata_path) if metadata_path.exists() else None
    if isinstance(metadata, dict):
        iso = _extract_iso_from_dict(metadata)
        if iso:
            return iso
    data_dir = project_path / 'data'
    candidate_dirs = [data_dir / 'rasters' / 'processed', data_dir / 'rasters' / 'raw', data_dir / 'vectors' / 'processed', data_dir / 'vectors' / 'raw']
    for directory in candidate_dirs:
        if not directory.exists():
            continue
        for json_path in directory.glob('**/*.json'):
            data = load_json_file(json_path)
            if isinstance(data, dict):
                iso = _extract_iso_from_dict(data)
                if iso:
                    return iso
    try:
        countries = _countries_from_project_metadata(project_path)
        if countries:
            return countries[0]
    except Exception:
        pass
    try:
        aoi_fc = _load_project_aoi_feature_collection(project_path)
        if isinstance(aoi_fc, dict):
            geom = _collect_geometry(aoi_fc)
            pt = geom.representative_point()
            iso, _ = _infer_country_from_point(float(pt.y), float(pt.x))
            normalized = _normalize_country_value(iso)
            if normalized:
                return normalized
    except Exception:
        pass
    try:
        if NATURAL_EARTH_ADMIN0_SHP.exists():
            aoi_fc = _load_project_aoi_feature_collection(project_path)
            if isinstance(aoi_fc, dict):
                jurisdictions = compute_aoi_jurisdictions(aoi_fc)
                countries = jurisdictions.get('countries_iso3') or []
                if countries:
                    return str(countries[0]).strip().upper()
    except Exception:
        pass
    return None

@lru_cache()
def _load_country_coverage_rows_cached(catalog_path: str, mtime: float) -> List[Dict[str, str]]:
    """
    Cached loader for dataset coverage catalog rows.

    Cache key includes `mtime` so updates to the CSV are picked up without a backend restart.
    """
    _ = mtime
    path = Path(catalog_path)
    if not path.exists():
        return []
    rows: List[Dict[str, str]] = []
    with path.open('r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows.extend(reader)
    return rows

def _try_build_dataset_coverage_catalog() -> None:
    """
    Best-effort: generate WORLD_DATASET_CATALOGUE.csv if it's missing.

    This keeps the Dataset Manager usable in fresh checkouts/minimal deployments where
    the consolidated catalogue hasn't been pre-generated yet.
    """
    if DATASET_COVERAGE_CATALOG_CSV.exists():
        return
    scripts_root = Path(__file__).resolve().parent.parent / 'scripts'
    builder = scripts_root / 'build_dataset_coverage_catalog.py'
    if not builder.exists():
        return
    try:
        subprocess.run([sys.executable, str(builder)], capture_output=True, text=True, timeout=90, check=False)
    except Exception:
        return

def _load_country_coverage_rows() -> List[Dict[str, str]]:
    """
    Load dataset coverage catalog rows.

    Prefer the consolidated catalogue (WORLD_DATASET_CATALOGUE.csv). If it's missing (fresh
    checkout / minimal deployment), fall back to the raw research table
    (COUNTRY_COVERAGE_LONG.csv) so country-specific datasets like TINITALY still surface.
    """
    if not DATASET_COVERAGE_CATALOG_CSV.exists():
        _try_build_dataset_coverage_catalog()
    catalog = DATASET_COVERAGE_CATALOG_CSV if DATASET_COVERAGE_CATALOG_CSV.exists() else COUNTRY_COVERAGE_LONG_CSV
    try:
        mtime = catalog.stat().st_mtime
    except OSError:
        mtime = -1.0
    return _load_country_coverage_rows_cached(str(catalog), float(mtime))

def _load_project_aoi_feature_collection(project_path: Path) -> Optional[Dict[str, Any]]:
    """
    Load the project's AOI as a GeoJSON FeatureCollection (WGS84).

    Many projects store AOI geometry in non-GeoJSON formats (e.g. GPKG/KML/KMZ) and/or
    reference it via `project_metadata.json` / `aoi/project_aoi.json`. This loader:
    - prefers the AOI file referenced by metadata when present
    - falls back to common AOI locations within the project
    - converts non-GeoJSON vector formats to GeoJSON (EPSG:4326) via ogr2ogr
    """
    candidates: List[Path] = []
    try:
        metadata_path = project_path / 'project_metadata.json'
        metadata = load_json_file(metadata_path) if metadata_path.exists() else None
        if isinstance(metadata, dict):
            normalized = _normalize_project_metadata(metadata, project_path)
            aoi_obj = normalized.get('aoi') if isinstance(normalized, dict) else None
            file_value: Optional[str] = None
            if isinstance(aoi_obj, dict):
                file_value = _sanitize_str(aoi_obj.get('file')) or _sanitize_str(aoi_obj.get('aoi_file'))
            if file_value:
                p = Path(file_value)
                candidates.append(p if p.is_absolute() else project_path / p)
    except Exception:
        pass
    candidates.extend([project_path / 'aoi' / 'aoi.geojson', project_path / 'aoi' / 'aoi.json', project_path / 'aoi' / 'aoi.gpkg', project_path / 'aoi' / 'aoi.kml', project_path / 'aoi' / 'aoi.kmz', project_path / 'data' / 'vectors' / 'aoi.gpkg'])
    processed_dir = project_path / 'data' / 'vectors' / 'processed'
    if processed_dir.exists():
        try:
            candidates.extend(sorted(processed_dir.glob('aoi_*_processed.gpkg')))
        except Exception:
            pass
    seen: set[str] = set()
    unique_candidates: List[Path] = []
    for p in candidates:
        try:
            key = str(p)
        except Exception:
            continue
        if not key or key in seen:
            continue
        seen.add(key)
        unique_candidates.append(p)
    for aoi_path in unique_candidates:
        try:
            if not aoi_path.exists():
                continue
        except Exception:
            continue
        try:
            suffix = aoi_path.suffix.lower()
            if suffix in {'.json', '.geojson'}:
                data = json.loads(aoi_path.read_text(encoding='utf-8'))
                if isinstance(data, dict) and data.get('type') == 'FeatureCollection':
                    return data
                if isinstance(data, dict) and data.get('type') == 'Feature':
                    return {'type': 'FeatureCollection', 'features': [data]}
                continue
            with tempfile.TemporaryDirectory() as tmpdir:
                fc = _convert_vector_to_geojson(aoi_path, Path(tmpdir))
                if isinstance(fc, dict) and fc.get('type') == 'FeatureCollection':
                    return fc
        except Exception:
            continue
    return None

def _countries_from_project_metadata(project_path: Path) -> List[str]:
    """
    Best-effort Admin0 fallback from project metadata (when AOI geometry isn't available).
    Returns unique ISO3 codes in stable order.
    """
    metadata_path = project_path / 'project_metadata.json'
    metadata = load_json_file(metadata_path) if metadata_path.exists() else None
    if not isinstance(metadata, dict) or not metadata:
        return []
    normalized = _normalize_project_metadata(metadata, project_path)
    values: List[str] = []
    if isinstance(normalized, dict):
        aoi_obj = normalized.get('aoi')
        if isinstance(aoi_obj, dict):
            aoi_countries = aoi_obj.get('countries')
            if isinstance(aoi_countries, list):
                values.extend([str(v) for v in aoi_countries if v is not None and str(v).strip()])
            elif isinstance(aoi_countries, str) and aoi_countries.strip():
                values.append(aoi_countries)
        iso3 = _sanitize_str(normalized.get('iso3'))
        country = _sanitize_str(normalized.get('country'))
        if iso3:
            values.append(iso3)
        if country:
            values.append(country)
    iso3s: List[str] = []
    seen_iso: set[str] = set()
    for v in values:
        iso = _normalize_country_value(v)
        if not iso:
            continue
        if iso in seen_iso:
            continue
        seen_iso.add(iso)
        iso3s.append(iso)
    return iso3s

def _row_to_entry(row: Dict[str, str], is_global: bool) -> DatasetCoverageEntry:

    def _maybe(field: str) -> Optional[str]:
        return _sanitize_str(row.get(field))
    return DatasetCoverageEntry(dataset=_maybe('Dataset') or 'Unnamed dataset', source=_maybe('Source'), data_type=_maybe('Type'), access=_maybe('Access'), coverage=_maybe('Coverage'), temporal_start=_maybe('TemporalStart'), temporal_end=_maybe('TemporalEnd'), frequency=_maybe('Frequency'), applies_globally=is_global, url=_maybe('URL'))

def _load_country_summary(country: Optional[str], iso3: Optional[str]) -> Optional[str]:
    candidates = []
    if country:
        candidates.append(country)
    if iso3:
        candidates.append(iso3)
    for candidate in candidates:
        sanitized = re.sub('[^A-Za-z0-9 _-]+', '', candidate or '').strip()
        if not sanitized:
            continue
        potential = COUNTRY_DATASETS_DIR / f'{sanitized}.txt'
        if potential.exists():
            try:
                return potential.read_text(encoding='utf-8').strip()
            except OSError:
                continue
    return None

@router.get('/projects/{project_name}/dataset-coverage', response_model=DatasetCoverageResponse)
async def get_project_dataset_coverage(project_name: str):
    """
    Return datasets that are known to cover the project's AOI boundaries.

    Coverage catalog is sourced from the unified CSV at:
      /opt/agrs/docs/Project Instructions/WORLD_DATASET_CATALOGUE.csv
    and honors the workflow defined in DATASET_FETCHING_PROTOCOLS.md.
    """
    project_path = resolve_project_path(project_name)
    if not project_path or not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")
    iso3_list: List[str] = []
    try:
        aoi_fc = _load_project_aoi_feature_collection(project_path)
        if isinstance(aoi_fc, dict) and NATURAL_EARTH_ADMIN0_SHP.exists():
            geom = _collect_geometry(aoi_fc)
            iso3_list = [str(c).strip().upper() for c in _aoi_countries_admin0(geom) if c]
    except Exception:
        pass
    if not iso3_list:
        try:
            aoi_fc = _load_project_aoi_feature_collection(project_path)
            if isinstance(aoi_fc, dict):
                geom = _collect_geometry(aoi_fc)
                pt = geom.representative_point()
                inferred, _ = _infer_country_from_point(float(pt.y), float(pt.x))
                norm = _normalize_country_value(inferred)
                if norm:
                    iso3_list = [norm]
        except Exception:
            pass
    if not iso3_list:
        single = _infer_project_iso3(project_path)
        if single:
            iso3_list = [single.strip().upper()]
    primary_iso3 = iso3_list[0] if iso3_list else 'WLD'
    iso_to_name, _, _ = _load_iso_mappings()
    country_names = [iso_to_name.get(c, c) for c in iso3_list]
    country_name = ', '.join((n for n in country_names if n)) or iso_to_name.get(primary_iso3)
    rows = _load_country_coverage_rows()
    if not rows:
        raise HTTPException(status_code=500, detail='Coverage catalog not available.')
    iso3_set = {c.upper() for c in iso3_list}
    local_rows: List[Dict[str, str]] = [] if not iso3_set or iso3_set == {'WLD'} else [row for row in rows if (row.get('ISO3') or '').strip().upper() in iso3_set]
    local_entries = [_row_to_entry(row, is_global=False) for row in local_rows]
    global_rows = [row for row in rows if (row.get('ISO3') or '').strip().upper() == 'WLD' and _global_row_applicable_to_project(row, primary_iso3)]
    global_entries = [_row_to_entry(row, is_global=True) for row in global_rows]
    entries: List[DatasetCoverageEntry] = []
    seen_dataset_names: set[str] = set()
    for entry in local_entries + global_entries:
        key = (entry.dataset or '').strip().lower()
        if not key or key in seen_dataset_names:
            continue
        seen_dataset_names.add(key)
        entries.append(entry)
    summary = _load_country_summary(country_name, primary_iso3)
    return DatasetCoverageResponse(iso3=primary_iso3, country=country_name, entries=entries, summary=summary, protocol_reference=DATASET_FETCH_PROTOCOL)

def _calculate_utm_zone(lon: float, lat: float) -> Tuple[int, str, int]:
    """
    Calculate UTM zone and EPSG code for a given longitude/latitude.
    Returns (zone_number, hemisphere_char, epsg_code).
    """
    zone = math.floor((lon + 180) / 6) + 1
    hemisphere = 'N' if lat >= 0 else 'S'
    base = 32600 if lat >= 0 else 32700
    epsg = base + zone
    return (zone, hemisphere, epsg)

def _get_project_bbox_latlon(project_path: Path) -> Optional[Tuple[float, float, float, float]]:
    """
    Get project AOI bounding box in WGS84 (min_lon, min_lat, max_lon, max_lat).
    """
    vectors_dir = project_path / 'data' / 'vectors' / 'processed'
    aoi_file = None
    if vectors_dir.exists():
        processed = list(vectors_dir.glob('aoi_*_processed.gpkg'))
        if processed:
            aoi_file = processed[0]
    if not aoi_file:
        symlink = project_path / 'data' / 'vectors' / 'aoi.gpkg'
        if symlink.exists():
            aoi_file = symlink
    if not aoi_file:
        raw_dir = project_path / 'aoi'
        if raw_dir.exists():
            candidates = list(raw_dir.glob('*.gpkg')) + list(raw_dir.glob('*.kml')) + list(raw_dir.glob('*.kmz')) + list(raw_dir.glob('*.geojson'))
            if candidates:
                aoi_file = candidates[0]
    if aoi_file and aoi_file.exists():
        try:
            cmd_geojson = ['ogr2ogr', '-f', 'GeoJSON', '-t_srs', 'EPSG:4326', '/vsistdout/', str(aoi_file)]
            result = subprocess.run(cmd_geojson, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                geojson = json.loads(result.stdout)
                if 'bbox' in geojson:
                    return tuple(geojson['bbox'])
                features = geojson.get('features', [])
                if features:
                    coords = []
                    for f in features:
                        geom = f.get('geometry', {})
                        if not geom:
                            continue

                        def extract_coords(obj):
                            if isinstance(obj, list):
                                if len(obj) >= 2 and isinstance(obj[0], (int, float)):
                                    coords.append(obj)
                                else:
                                    for item in obj:
                                        extract_coords(item)
                        extract_coords(geom.get('coordinates', []))
                    if coords:
                        lons = [c[0] for c in coords]
                        lats = [c[1] for c in coords]
                        return (min(lons), min(lats), max(lons), max(lats))
        except Exception as e:
            print(f'Failed to extract bbox from {aoi_file}: {e}')
            pass
    return None

@router.get('/projects/{project_name}/crs/recommend', response_model=ProjectCRSRecommendation)
async def recommend_project_crs(project_name: str):
    """
    Analyze project AOI and recommend the best Coordinate Reference System (UTM).
    """
    project_path = resolve_project_path(project_name)
    if not project_path or not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")
    bbox = _get_project_bbox_latlon(project_path)
    if not bbox:
        iso3 = _infer_project_iso3(project_path)
        if iso3 == 'USA':
            return ProjectCRSRecommendation(epsg=4269, name='NAD83', reason='Country default for USA (AOI not found)')
        return ProjectCRSRecommendation(epsg=4326, name='WGS 84', reason='AOI not defined, defaulting to Geographic WGS 84')
    min_lon, min_lat, max_lon, max_lat = bbox
    center_lon = (min_lon + max_lon) / 2
    center_lat = (min_lat + max_lat) / 2
    zone, hemi, epsg = _calculate_utm_zone(center_lon, center_lat)
    crs_name = f'WGS 84 / UTM zone {zone}{hemi}'
    return ProjectCRSRecommendation(epsg=epsg, name=crs_name, reason=f'Best fit for AOI centroid ({center_lat:.2f}, {center_lon:.2f}) in UTM Zone {zone}{hemi}', utm_zone=zone, hemisphere=hemi)

@router.post('/projects/create')
async def create_project(project_name: str=Form(...), organization: str=Form(''), project_creator: str=Form(''), measurement_system: str=Form('SI'), aoi_file: Optional[UploadFile]=File(None), drawn_geojson: Optional[str]=Form(None), crs_epsg: Optional[int]=Form(None), crs_name: Optional[str]=Form(None), workspace_directory: Optional[str]=Form(None)):
    from pyproj import CRS
    from .project_utils import get_projects_root
    name = _sanitize_project_name(project_name)
    if not name or name in {'.', '..'}:
        raise HTTPException(400, 'Please provide a valid project name.')
    root = get_projects_root().resolve()
    if workspace_directory is not None and Path(workspace_directory).resolve() != root:
        raise HTTPException(409, 'The save directory changed. Reopen project creation to review its location.')
    directory = (root / name).resolve()
    if directory.parent != root:
        raise HTTPException(400, 'Invalid project name.')
    if directory.exists() or name in discover_project_paths(force_refresh=True):
        raise HTTPException(409, 'A project with this name already exists.')
    if measurement_system not in {'SI', 'Imperial'}:
        raise HTTPException(400, 'Unknown measurement system.')
    with tempfile.TemporaryDirectory() as temp:
        aoi = _prepare_aoi_payload(aoi_file, drawn_geojson, Path(temp))
        geometry = _collect_geometry(aoi)
        if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in {'Polygon', 'MultiPolygon'}:
            raise HTTPException(400, 'The area of interest must be a valid polygon.')
        west, south, east, north = geometry.bounds
        if west < -180 or east > 180 or south < -90 or (north > 90):
            raise HTTPException(400, 'AOI coordinates must be longitude and latitude (WGS 84).')
        centroid = geometry.centroid
        countries = sorted(set(_aoi_countries_admin0(geometry)))
        if countries:
            iso3 = countries[0]
            country = pycountry.countries.get(alpha_3=iso3).name if pycountry.countries.get(alpha_3=iso3) else iso3
        else:
            iso3, country = _infer_country_from_point(centroid.y, centroid.x)
            countries = [iso3] if iso3 else []
        country_names = [pycountry.countries.get(alpha_3=code).name if pycountry.countries.get(alpha_3=code) else code for code in countries]
        _, _, default_epsg = _calculate_utm_zone(centroid.x, centroid.y)
        try:
            crs = CRS.from_epsg(crs_epsg or default_epsg)
        except Exception:
            raise HTTPException(400, 'Invalid coordinate reference system.')
        area = _calculate_area_km2(aoi)
    epsg = crs.to_epsg()
    project_id = _generate_project_id(organization, name, iso3)
    metadata = {'project_name': name, 'project_id': project_id, 'organization': organization, 'project_creator': project_creator, 'measurement_system': measurement_system, 'date_created': datetime.utcnow().isoformat() + 'Z', 'status': 'active', 'iso3': iso3, 'iso3_list': countries, 'country': country, 'crs': {'epsg': epsg, 'name': crs.name}}
    try:
        directory.mkdir(parents=True)
        for subdir in ['aoi', 'data/rasters/raw', 'data/rasters/processed', 'data/vectors/raw', 'data/vectors/processed', 'logs']:
            (directory / subdir).mkdir(parents=True, exist_ok=True)
        _write_json(directory / 'project_metadata.json', metadata)
        _write_json(directory / 'aoi/aoi.geojson', aoi)
        _write_json(directory / 'aoi/project_aoi.json', {'aoi_file': 'aoi/aoi.geojson', 'aoi_area_km2': area, 'aoi_countries': country_names, 'countries_iso3': countries, 'crs_epsg': epsg, 'crs_name': crs.name})
        result = subprocess.run(['ogr2ogr', '-f', 'GPKG', '-t_srs', f'EPSG:{epsg}', str(directory / f'data/vectors/processed/aoi_epsg{epsg}_processed.gpkg'), str(directory / 'aoi/aoi.geojson')], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr)
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(500, f'Project could not be created: {exc}')
    discover_project_paths(force_refresh=True)
    return {'project_name': name, 'project_id': project_id, 'project_path': str(directory), 'metadata': metadata}


class CRSUpdate(BaseModel):
    epsg: int
    name: Optional[str] = None


@router.put('/projects/{project_name}/crs')
def update_project_crs(project_name: str, request: CRSUpdate):
    from pyproj import CRS
    directory = resolve_project_path(project_name)
    if not directory:
        raise HTTPException(404, 'Project not found.')
    try:
        crs = CRS.from_epsg(request.epsg)
    except Exception:
        raise HTTPException(400, 'Invalid EPSG code.')
    metadata = load_json_file(directory / 'project_metadata.json') or {}
    if metadata.get('crs', {}).get('epsg') == request.epsg:
        return {'status': 'unchanged', 'epsg': request.epsg, 'name': crs.name}
    artifacts = [p for p in (directory / 'data').rglob('*') if p.suffix in {'.tif', '.gpkg'} and not p.name.startswith('aoi_')]
    if artifacts:
        raise HTTPException(409, 'This project already has datasets in its selected CRS. Create a new project with the desired CRS to keep existing data consistent.')
    target = directory / f'data/vectors/processed/aoi_epsg{request.epsg}_processed.gpkg'
    result = subprocess.run(['ogr2ogr', '-f', 'GPKG', '-overwrite', '-t_srs', f'EPSG:{request.epsg}', str(target), str(directory / 'aoi/aoi.geojson')], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise HTTPException(500, 'Could not reproject the project AOI.')
    metadata['crs'] = {'epsg': request.epsg, 'name': crs.name}
    _write_json(directory / 'project_metadata.json', metadata)
    aoi_metadata = load_json_file(directory / 'aoi/project_aoi.json') or {}
    aoi_metadata.update(crs_epsg=request.epsg, crs_name=crs.name)
    _write_json(directory / 'aoi/project_aoi.json', aoi_metadata)
    for previous in target.parent.glob('aoi_*_processed.gpkg'):
        if previous != target:
            previous.unlink()
    return {'status': 'updated', 'epsg': request.epsg, 'name': crs.name}
