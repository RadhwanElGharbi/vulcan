import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse, Response
from PIL import Image
import tifffile
from .project_utils import resolve_project_path
router = APIRouter()
GEOJSON_CACHE = {}
TILE_CACHE_ROOT = Path(os.getenv('AGRS_TILE_CACHE_DIR', str(Path(__file__).resolve().parents[3] / '.runtime/tiles')))
RASTER_DISPLAY_POLICY = 'target-pixel-aoi-mask/2'
VECTOR_TILE_MINZOOM = int(os.getenv('AGRS_VECTOR_TILE_MINZOOM', '0'))
VECTOR_TILE_MAXZOOM = int(os.getenv('AGRS_VECTOR_TILE_MAXZOOM', '11'))

def tile_cache_root():
    from .cloud_workspace import workspace_path
    workspace = workspace_path()
    return workspace / 'tiles' if workspace else TILE_CACHE_ROOT


def get_cloud_project_root():
    from .cloud_workspace import workspace_path
    workspace = workspace_path()
    return workspace / 'Projects' if workspace else None


def get_project_path_or_404(project: str) -> Path:
    """Resolve a project path or raise a 404 HTTPException."""
    project_path = resolve_project_path(project)
    if not project_path or not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found (missing project_metadata.json)")
    return project_path

def _safe_segment(value: str) -> str:
    sanitized = value.replace('..', '__').replace('/', '_').replace(chr(92), '_').strip()
    return sanitized or 'default'

def _ensure_cache_root() -> None:
    tile_cache_root().mkdir(parents=True, exist_ok=True)

def _purge_directory_contents(directory: Path) -> None:
    if not directory.exists():
        return
    for child in directory.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except FileNotFoundError:
                continue

def _ensure_version_dir(base_dir: Path, version: str) -> Path:
    """
    Ensure a cache directory exists for the current dataset version and
    drop stale versions to keep disk usage bounded.
    """
    _ensure_cache_root()
    version_dir = base_dir / version
    if version_dir.exists():
        return version_dir
    base_dir.mkdir(parents=True, exist_ok=True)
    _purge_directory_contents(base_dir)
    version_dir.mkdir(parents=True, exist_ok=True)
    return version_dir

def _write_cache_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.' + uuid.uuid4().hex + '.tmp')
    with open(tmp_path, 'wb') as tmp_file:
        tmp_file.write(data)
    os.replace(tmp_path, path)
    import hashlib
    from .dataset_fetch.research.store import atomic_json
    atomic_json(path.with_suffix(path.suffix+'.integrity.json'), {'sha256': hashlib.sha256(data).hexdigest()})

def _read_cache_file(path: Path):
    import hashlib
    try:
        data = path.read_bytes()
        record = json.loads(path.with_suffix(path.suffix+'.integrity.json').read_text())
        return data if hashlib.sha256(data).hexdigest() == record['sha256'] else None
    except (OSError, ValueError, KeyError):
        return None

def _tile_cache_path(kind: str, project: str, layer: str, mtime_ns: int, z: int, x: int, y: int) -> Path:
    base_dir = tile_cache_root() / kind / _safe_segment(project) / _safe_segment(layer)
    version_dir = _ensure_version_dir(base_dir, str(int(mtime_ns)))
    tile_dir = version_dir / str(z) / str(x)
    tile_dir.mkdir(parents=True, exist_ok=True)
    return tile_dir / f'{y}.png'

def _vector_cache_file(project: str, layer: str, mtime_ns: int) -> Path:
    base_dir = tile_cache_root() / 'vectors' / _safe_segment(project) / _safe_segment(layer)
    return base_dir / f'{int(mtime_ns)}.geojson'

def _expand_other_tags(geojson_data: dict) -> dict:
    """
    Previously expanded the 'other_tags' hstore column from OSM data into individual columns.
    Now disabled to preserve the original attribute structure as requested by the user.
    The attributes table should display exactly as the raw data is structured.
    """
    return geojson_data

def _dataset_mtime(path: Path) -> Tuple[float, int]:
    if 'generations' in path.parts:
        from .dataset_fetch.research.contracts import file_hash
        identity = int(file_hash(path), 16)
        return (identity, identity)
    stat_info = path.stat()
    mtime = stat_info.st_mtime
    mtime_ns = getattr(stat_info, 'st_mtime_ns', int(mtime * 1000000000))
    return (mtime, int(mtime_ns))

def _vector_tileset_dir(project: str, layer: str, mtime_ns: int) -> Path:
    base_dir = tile_cache_root() / 'vector_mvt' / _safe_segment(project) / _safe_segment(layer)
    return base_dir / str(int(mtime_ns))

def _vector_tile_path(project: str, layer: str, mtime_ns: int, z: int, x: int, y: int) -> Path:
    tileset_dir = _vector_tileset_dir(project, layer, mtime_ns)
    return tileset_dir / str(z) / str(x) / f'{y}.pbf'

def _ensure_vector_tileset(project: str, layer: str, vector_file: Path, mtime_ns: int) -> Path:
    """
    Ensure an on-disk MVT tileset exists for this vector dataset version.

    This is required for very large layers (e.g., NHN waterways) because serving full GeoJSON
    is not feasible in MapLibre.
    """
    tileset_dir = _vector_tileset_dir(project, layer, mtime_ns)
    sentinel = tileset_dir / '.complete'
    def completed():
        try:
            return json.loads(sentinel.read_text())['source_identity'] == str(mtime_ns)
        except (OSError, ValueError, KeyError):
            return False
    if completed():
        return tileset_dir
    base_dir = tileset_dir.parent
    lock_path = base_dir / '.build.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a+b') as lock_handle:
        lock_handle.seek(0)
        if not lock_handle.read(1):
            lock_handle.write(b'0'); lock_handle.flush()
        start = time.time()
        while True:
            try:
                lock_handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() - start > 1800:
                    raise TimeoutError(f'Timed out waiting for vector tileset build lock: {lock_path}')
                time.sleep(0.2)
        if completed():
            return tileset_dir
        _ensure_cache_root()
        base_dir.mkdir(parents=True, exist_ok=True)
        if not base_dir.resolve().is_relative_to(tile_cache_root().resolve()):
            raise ValueError('Vector cache path escaped the configured cache root')
        if base_dir.exists():
            for child in base_dir.iterdir():
                if child.is_dir() and not child.is_symlink() and child.resolve().is_relative_to(base_dir.resolve()) and child.name != str(int(mtime_ns)):
                    shutil.rmtree(child, ignore_errors=True)
        if tileset_dir.exists():
            shutil.rmtree(tileset_dir, ignore_errors=True)
        tmp_out = base_dir / f'.building-{uuid.uuid4().hex}'
        if tmp_out.exists():
            shutil.rmtree(tmp_out, ignore_errors=True)
        cmd = ['ogr2ogr', '-f', 'MVT', str(tmp_out), str(vector_file), '-dsco', 'FORMAT=DIRECTORY', '-lco', f'MINZOOM={VECTOR_TILE_MINZOOM}', '-lco', f'MAXZOOM={VECTOR_TILE_MAXZOOM}', '-lco', f'NAME={layer}', '-dim', '2']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            shutil.rmtree(tmp_out, ignore_errors=True)
            raise RuntimeError(f'ogr2ogr MVT tileset generation failed: {(result.stderr or result.stdout).strip()}')
        tmp_out.replace(tileset_dir)
        from .dataset_fetch.research.contracts import file_hash
        from .dataset_fetch.research.store import atomic_json
        atomic_json(sentinel, {'source_identity':str(mtime_ns),'tiles':{p.relative_to(tileset_dir).as_posix():file_hash(p) for p in sorted(tileset_dir.rglob('*.pbf'))}})
        return tileset_dir

def mercator_tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """
    Calculate Web Mercator bounds for a given XYZ tile.
    Returns (minx, miny, maxx, maxy) in EPSG:3857 meters.
    """
    n = 2 ** z
    lon_left = x / n * 360.0 - 180.0
    lon_right = (x + 1) / n * 360.0 - 180.0

    def lat_for_tile(y_val: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y_val / n))))
    lat_top = lat_for_tile(y)
    lat_bottom = lat_for_tile(y + 1)

    def to_mercator(lon: float, lat: float) -> tuple[float, float]:
        lat = max(min(lat, 85.05112878), -85.05112878)
        x_merc = lon * 20037508.342789244 / 180.0
        y_merc = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0
        return (x_merc, y_merc)
    min_x, max_y = to_mercator(lon_left, lat_top)
    max_x, min_y = to_mercator(lon_right, lat_bottom)
    return (min_x, min_y, max_x, max_y)

def read_nodata_from_sidecar(raster_file: Path) -> Optional[float]:
    """Read nodata value from metadata sidecar or fall back to GDAL metadata."""
    sidecar = raster_file.with_suffix(raster_file.suffix + '.json')
    if sidecar.exists():
        try:
            with open(sidecar, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            nodata = meta.get('nodata_value')
            if isinstance(nodata, (int, float)):
                return float(nodata)
        except Exception:
            pass
    try:
        result = subprocess.run(['gdalinfo', '-json', str(raster_file)], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        metadata = json.loads(result.stdout)
        bands = metadata.get('bands', [])
        if bands:
            nodata = bands[0].get('noDataValue')
            if isinstance(nodata, (int, float)):
                return float(nodata)
    except Exception:
        return None
    return None

@lru_cache(maxsize=256)
def get_raster_band_profile(path: str) -> dict:
    """
    Inspect a raster once to determine band count, data type, and palette usage.
    Cached per file path to avoid repeated gdalinfo calls.
    """
    result = subprocess.run(['gdalinfo', '-json', path], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'gdalinfo failed for {path}: {result.stderr}')
    metadata = json.loads(result.stdout)
    bands = metadata.get('bands', [])
    color_interps = [band.get('colorInterpretation', '') for band in bands]
    has_palette = any((ci.lower() == 'palette' for ci in color_interps))
    data_type = 'Byte'
    if bands:
        data_type = bands[0].get('type', 'Byte')
    return {'band_count': len(bands), 'color_interps': color_interps, 'has_palette': has_palette, 'data_type': data_type}

@lru_cache(maxsize=256)
def _get_raster_statistics(path: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Get global min/max statistics for a raster.
    Uses gdalinfo -stats to compute or retrieve statistics.
    Returns (min, max) tuple or (None, None) if unavailable.
    """
    try:
        result = subprocess.run(['gdalinfo', '-json', '-stats', path], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return (None, None)
        metadata = json.loads(result.stdout)
        bands = metadata.get('bands', [])
        if not bands:
            return (None, None)
        band = bands[0]
        stat_min = band.get('minimum')
        stat_max = band.get('maximum')
        if stat_min is not None and stat_max is not None:
            return (float(stat_min), float(stat_max))
        stat_min = band.get('computedMin')
        stat_max = band.get('computedMax')
        if stat_min is not None and stat_max is not None:
            return (float(stat_min), float(stat_max))
        return (None, None)
    except Exception:
        return (None, None)

def _build_display_name_from_metadata(metadata: dict, fallback_name: str) -> str:
    """
    Build display name from metadata JSON sidecar.
    Format: {category}_{dataset_name}_{target_crs}_processed
    Where dataset_name has spaces replaced with hyphens.
    target_crs is formatted as EPSGnumber (no colon).
    """
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

def _find_vector_file(project_path: Path, layer: str) -> Optional[Path]:
    """
    Find a vector file by layer name (display name or filename-based name).
    Checks processed/ first, then legacy location.
    """
    import re
    from .dataset_fetch.research.readers import resolve_active
    active = resolve_active(project_path, layer, 'vector')
    if active:
        return active
    vectors_processed_dir = project_path / 'data' / 'vectors' / 'processed'
    vectors_dir = project_path / 'data' / 'vectors'
    if vectors_processed_dir.exists():
        candidate = vectors_processed_dir / f'{layer}.gpkg'
        if candidate.exists():
            return candidate
        for item in vectors_processed_dir.iterdir():
            if item.suffix == '.gpkg':
                metadata_file = item.with_name(f'{item.name}.json')
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                        raw_name = item.stem
                        fallback = re.sub('_epsg\\d+_processed$', '', raw_name, flags=re.IGNORECASE)
                        fallback = re.sub('_processed$', '', fallback, flags=re.IGNORECASE)
                        display_name = _build_display_name_from_metadata(metadata, fallback)
                        if display_name == layer:
                            return item
                    except Exception:
                        pass
                raw_name = item.stem
                fallback_name = re.sub('_epsg\\d+_processed$', '', raw_name, flags=re.IGNORECASE)
                fallback_name = re.sub('_processed$', '', fallback_name, flags=re.IGNORECASE)
                if fallback_name == layer:
                    return item
    legacy_file = vectors_dir / f'{layer}.gpkg'
    if legacy_file.exists():
        return legacy_file
    return None

def _load_vector_geojson(project: str, layer: str) -> dict:
    """
    Load a project's vector layer as GeoJSON (dict), reusing the same caching/conversion
    logic as the public GET endpoint.
    """
    project_path = get_project_path_or_404(project)
    vector_file = _find_vector_file(project_path, layer)
    if vector_file is None or not vector_file.exists():
        raise HTTPException(status_code=404, detail=f"Vector layer '{layer}' not found in project '{project}'")
    _, vector_mtime_ns = _dataset_mtime(vector_file)
    cache_key = f'{project_path.resolve()}:{layer}:{vector_mtime_ns}'
    cached = GEOJSON_CACHE.get(cache_key)
    if isinstance(cached, dict):
        return cached
    cache_file = _vector_cache_file(project, layer, vector_mtime_ns)
    if cache_file.exists():
        try:
            data = _read_cache_file(cache_file)
            geojson_data = json.loads(data) if data is not None else None
            if isinstance(geojson_data, dict):
                GEOJSON_CACHE[cache_key] = geojson_data
                return geojson_data
        except Exception:
            pass
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as tmp_file:
            tmp_path = tmp_file.name
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        cmd = ['ogr2ogr', '-f', 'GeoJSON', '-t_srs', 'EPSG:4326', tmp_path, str(vector_file)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f'ogr2ogr failed: {result.stderr}')
        with open(tmp_path, 'r', encoding='utf-8') as f:
            geojson_data = json.load(f)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        geojson_data = _expand_other_tags(geojson_data)
        if not isinstance(geojson_data, dict):
            raise Exception('Converted GeoJSON is not an object.')
        GEOJSON_CACHE[cache_key] = geojson_data
        if not cache_file.exists():
            cache_dir = cache_file.parent
            cache_dir.mkdir(parents=True, exist_ok=True)
            _purge_directory_contents(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            _write_cache_file(cache_file, json.dumps(geojson_data).encode())
        except Exception:
            pass
        return geojson_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to convert vector layer: {str(e)}')

@router.get('/data/{project}/vectors/{layer}')
async def get_vector_layer(project: str, layer: str):
    """
    Get a vector layer as GeoJSON
    
    Converts GeoPackage to GeoJSON on-the-fly using ogr2ogr.
    Results are cached for performance.
    
    Looks in data/vectors/processed/ first (canonical location),
    then falls back to data/vectors/ for legacy symlinks.
    Matches by display name (from metadata) or filename pattern.
    """
    geojson_data = _load_vector_geojson(project, layer)
    return JSONResponse(content=geojson_data)

@router.post('/data/{project}/vectors/{layer}/nearest')
async def get_vector_nearest_features(project: str, layer: str, payload: dict=Body(...)):
    """
    Return the nearest vector features to a target geometry.

    - For AOI polygons: prefer features that intersect the AOI; if fewer than limit, fill
      remaining with nearest outside the AOI boundary.
    - For POI points: nearest by distance to point.
    """
    target_geometry = payload.get('target_geometry')
    limit_raw = payload.get('limit', 50)
    if not isinstance(target_geometry, dict):
        raise HTTPException(status_code=400, detail='target_geometry must be a GeoJSON Geometry object.')
    try:
        limit = int(limit_raw)
    except Exception:
        raise HTTPException(status_code=400, detail='limit must be an integer.')
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail='limit must be between 1 and 200.')
    geojson_data = _load_vector_geojson(project, layer)
    features = geojson_data.get('features')
    if not isinstance(features, list):
        raise HTTPException(status_code=500, detail='Vector dataset has invalid GeoJSON features.')
    try:
        from shapely.geometry import shape as shp_shape
        from shapely.ops import transform as shp_transform
        from pyproj import Transformer
    except Exception as exc:
        raise HTTPException(status_code=500, detail='Spatial dependencies not available for nearest-feature query.') from exc
    try:
        target_geom = shp_shape(target_geometry)
    except Exception as exc:
        raise HTTPException(status_code=400, detail='Invalid target_geometry.') from exc
    transformer = Transformer.from_crs('EPSG:4326', 'EPSG:3857', always_xy=True)
    target_m = shp_transform(lambda x, y: transformer.transform(x, y), target_geom)
    is_aoi = target_geom.geom_type in {'Polygon', 'MultiPolygon'}
    target_centroid_m = target_m.centroid if is_aoi else None
    inside: list[tuple[float, float, dict]] = []
    outside: list[tuple[float, float, dict]] = []
    all_candidates: list[tuple[float, dict]] = []
    for idx, feat in enumerate(features):
        if not isinstance(feat, dict):
            continue
        geom_raw = feat.get('geometry')
        if not isinstance(geom_raw, dict):
            continue
        try:
            geom = shp_shape(geom_raw)
        except Exception:
            continue
        try:
            geom_m = shp_transform(lambda x, y: transformer.transform(x, y), geom)
        except Exception:
            continue
        if 'id' not in feat or feat.get('id') is None:
            props = feat.get('properties') if isinstance(feat.get('properties'), dict) else {}
            feat_id = props.get('id') or props.get('ID') or props.get('fid') or props.get('FID') or props.get('osm_id') or None
            feat['id'] = feat_id if feat_id is not None else f'{layer}:{idx}'
        within_aoi = False
        try:
            if is_aoi:
                within_aoi = bool(geom.intersects(target_geom))
        except Exception:
            within_aoi = False
        try:
            distance_m = float(geom_m.distance(target_m))
        except Exception:
            continue
        tie_m = distance_m
        if is_aoi and within_aoi and (target_centroid_m is not None):
            try:
                tie_m = float(geom_m.distance(target_centroid_m))
            except Exception:
                tie_m = distance_m
        candidate = {'rank': 0, 'within_aoi': within_aoi, 'distance_m': distance_m, 'feature': feat}
        if is_aoi:
            (inside if within_aoi else outside).append((distance_m, tie_m, candidate))
        else:
            all_candidates.append((distance_m, candidate))
    if is_aoi:
        inside_sorted = sorted(inside, key=lambda t: (float(t[0]), float(t[1])))
        outside_sorted = sorted(outside, key=lambda t: (float(t[0]), float(t[1])))
        picked = [t[2] for t in inside_sorted[:limit]]
        if len(picked) < limit:
            picked.extend([t[2] for t in outside_sorted[:limit - len(picked)]])
    else:
        picked = [t[1] for t in sorted(all_candidates, key=lambda t: float(t[0]))[:limit]]
    for i, c in enumerate(picked, start=1):
        c['rank'] = i
    return {'dataset': {'project': project, 'layer': layer}, 'candidates': picked}

def _display_aoi_alpha(cutline_path: Path, bounds) -> np.ndarray:
    """Mask target display pixels, retaining AOIs smaller than native cells."""
    from osgeo import gdal, osr
    min_x,min_y,max_x,max_y=bounds
    target=gdal.GetDriverByName('MEM').Create('',256,256,1,gdal.GDT_Byte)
    target.SetGeoTransform((min_x,(max_x-min_x)/256,0,max_y,0,-(max_y-min_y)/256))
    crs=osr.SpatialReference();crs.ImportFromEPSG(3857);target.SetProjection(crs.ExportToWkt())
    target.GetRasterBand(1).Fill(0)
    # GDAL transforms the retained geographic polygon, including holes, into
    # this display grid. Scientific arrays and their native masks are untouched.
    if gdal.Rasterize(target,str(cutline_path),burnValues=[255])!=1:
        raise ValueError('Cannot rasterize the frozen AOI for display')
    alpha=target.ReadAsArray();target=None
    if alpha is None or alpha.shape!=(256,256):raise ValueError('Incomplete AOI display mask')
    return alpha


def render_raster_tile(raster_file: Path, z: int, x: int, y: int, aoi_json=None, gap_mask=None, categorical=None) -> bytes:
    """Render a single raster tile as PNG using gdalwarp and numpy for color mapping."""
    min_x, min_y, max_x, max_y = mercator_tile_bounds(z, x, y)
    with tempfile.NamedTemporaryFile(suffix='.tif', delete=False) as warp_tmp:
        warp_path = Path(warp_tmp.name)
    nodata_value = read_nodata_from_sidecar(raster_file)
    band_profile = get_raster_band_profile(str(raster_file))
    band_count = band_profile['band_count']
    has_palette = band_profile['has_palette']
    data_type = band_profile.get('data_type', 'Byte')
    is_continuous_data = band_count == 1 and (not has_palette) and (data_type in ('Float32', 'Float64', 'Int16', 'Int32', 'UInt16', 'UInt32'))
    cutline_path=None
    try:
        warp_cmd = ['gdalwarp', '-t_srs', 'EPSG:3857', '-te', str(min_x), str(min_y), str(max_x), str(max_y), '-te_srs', 'EPSG:3857', '-ts', '256', '256', '-r', 'near' if has_palette or categorical else 'bilinear', '-of', 'GTiff', '-dstalpha']
        if aoi_json:
            with tempfile.NamedTemporaryFile(mode='w',suffix='.geojson',delete=False,encoding='utf-8') as cutline:
                cutline_path=Path(cutline.name)
                json.dump({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':json.loads(aoi_json)}]},cutline)
        if nodata_value is not None:
            nodata_str = str(nodata_value)
            warp_cmd.extend(['-srcnodata', nodata_str, '-dstnodata', nodata_str])
        warp_cmd.extend([str(raster_file), str(warp_path)])
        result = subprocess.run(warp_cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f'gdalwarp failed: {result.stderr}')
        alpha_mask = _read_alpha_mask(warp_path)
        if cutline_path:
            aoi_alpha=_display_aoi_alpha(cutline_path,(min_x,min_y,max_x,max_y))
            alpha_mask=aoi_alpha if alpha_mask is None else np.minimum(alpha_mask,aoi_alpha)
        if gap_mask:
            from .dataset_fetch.research.preview import validity_alpha
            validity=validity_alpha(gap_mask,(min_x,min_y,max_x,max_y))
            alpha_mask=validity if alpha_mask is None else np.minimum(alpha_mask,validity)
        if is_continuous_data:
            global_min, global_max = _get_raster_statistics(str(raster_file))
            return _render_continuous_raster_tile(warp_path, nodata_value, global_min, global_max, alpha_mask)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as png_tmp:
            png_path = Path(png_tmp.name)
        try:
            add_alpha = not has_palette and (band_count == 1 or band_count == 3)
            if add_alpha:
                warp_cmd_alpha = warp_cmd[:-2] + ['-dstalpha'] + warp_cmd[-2:]
                result = subprocess.run(warp_cmd_alpha, capture_output=True)
            translate_cmd = ['gdal_translate', '-of', 'PNG', '-outsize', '256', '256']
            if has_palette:
                translate_cmd.extend(['-expand', 'rgba'])
            else:
                total_bands = band_count + 1
                max_bands = max(1, min(total_bands, 4))
                for band_index in range(1, max_bands + 1):
                    translate_cmd.extend(['-b', str(band_index)])
            if nodata_value is not None and (not has_palette):
                translate_cmd.extend(['-a_nodata', str(nodata_value)])
            translate_cmd.extend([str(warp_path), str(png_path)])
            result = subprocess.run(translate_cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f'gdal_translate failed: {result.stderr}')
            tile_bytes = _apply_alpha_mask_to_png(png_path, alpha_mask)
            return tile_bytes
        finally:
            if png_path.exists():
                os.unlink(png_path)
    finally:
        if warp_path.exists():
            os.unlink(warp_path)
        if cutline_path and cutline_path.exists():cutline_path.unlink()

def _render_continuous_raster_tile(warp_path: Path, nodata_value: Optional[float], global_min: Optional[float]=None, global_max: Optional[float]=None, alpha_mask: Optional[np.ndarray]=None) -> bytes:
    """
    Render a continuous data raster (DEM, etc.) with proper color scaling.
    Uses a terrain color ramp for elevation-like data.
    
    If global_min/global_max are provided, uses those for normalization
    to ensure consistent colors across all tiles.
    """
    with tifffile.TiffFile(warp_path) as tif:
        arr = tif.asarray()
    alpha_band = None
    if arr.ndim == 3:
        if arr.shape[0] <= 4 and arr.shape[0] < arr.shape[-1]:
            data = arr[0].astype(np.float32)
            alpha_band = arr[-1]
        else:
            data = arr[:, :, 0].astype(np.float32)
            if arr.shape[2] > 1:
                alpha_band = arr[:, :, -1]
    elif arr.ndim == 2:
        data = arr.astype(np.float32)
    else:
        data = arr[0].astype(np.float32)
    mask = np.ones_like(data, dtype=bool)
    if alpha_mask is None and alpha_band is not None:
        alpha_mask = alpha_band
    if alpha_mask is not None:
        mask &= alpha_mask > 0
    if nodata_value is not None:
        mask &= data != nodata_value
    mask &= ~np.isnan(data)
    mask &= ~np.isinf(data)
    if not mask.any():
        rgba = np.zeros((256, 256, 4), dtype=np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(rgba, mode='RGBA').save(buffer, format='PNG', compress_level=6)
        return buffer.getvalue()
    if global_min is not None and global_max is not None:
        data_min = global_min
        data_max = global_max
    else:
        valid_data = data[mask]
        data_min = float(np.min(valid_data))
        data_max = float(np.max(valid_data))
    if data_max == data_min:
        data_max = data_min + 1.0
    normalized = np.zeros_like(data)
    normalized[mask] = (data[mask] - data_min) / (data_max - data_min)
    normalized = np.clip(normalized, 0, 1)
    gray = (normalized * 255).astype(np.uint8)
    height, width = normalized.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[:, :, 0] = gray
    rgba[:, :, 1] = gray
    rgba[:, :, 2] = gray
    rgba[:, :, 3] = np.where(mask, 255, 0).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode='RGBA').save(buffer, format='PNG', compress_level=6)
    return buffer.getvalue()

def _read_alpha_mask(warp_path: Path) -> Optional[np.ndarray]:
    try:
        with tifffile.TiffFile(warp_path) as tif:
            arr = tif.asarray()
    except Exception:
        return None
    mask = None
    if arr.ndim == 3:
        if arr.shape[0] <= 4 and arr.shape[0] < arr.shape[-1]:
            mask = arr[-1]
        else:
            mask = arr[:, :, -1]
    elif arr.ndim == 2:
        mask = np.full(arr.shape, 255, dtype=np.uint8)
    else:
        mask = arr[-1]
    if mask is None:
        return None
    mask = np.clip(mask, 0, 255).astype(np.uint8)
    return mask

def _apply_alpha_mask_to_png(png_path: Path, mask: Optional[np.ndarray]) -> bytes:
    with open(png_path, 'rb') as f:
        png_bytes = f.read()
    if mask is None:
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
    rgba = np.array(img)
    if mask.shape != rgba.shape[:2]:
        mask_img = Image.fromarray(mask)
        mask = np.array(mask_img.resize((rgba.shape[1], rgba.shape[0]), Image.NEAREST))
    new_alpha = (rgba[:, :, 3].astype(np.uint16) * mask.astype(np.uint16) // 255).astype(np.uint8)
    rgba[:, :, 3] = new_alpha
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode='RGBA').save(buffer, format='PNG', compress_level=6)
    return buffer.getvalue()

def encode_mapbox_terrain(elevation: np.ndarray, nodata_value: float) -> np.ndarray:
    """Convert elevation array (meters) into Mapbox terrain-RGB encoding."""
    data = np.array(elevation, dtype=np.float32)
    mask = np.ones_like(data, dtype=bool)
    if nodata_value is not None:
        mask &= data != nodata_value
    mask &= np.isfinite(data)
    clipped = np.clip(np.nan_to_num(data, nan=nodata_value if nodata_value is not None else -32768.0), -10000.0, 9000.0)
    encoded = np.round((clipped + 10000.0) * 10.0).astype(np.uint32)
    encoded[~mask] = 0
    r = (encoded >> 16 & 255).astype(np.uint8)
    g = (encoded >> 8 & 255).astype(np.uint8)
    b = (encoded & 255).astype(np.uint8)
    a = np.where(mask, 255, 0).astype(np.uint8)
    return np.dstack([r, g, b, a])

def render_terrain_tile(raster_file: Path, z: int, x: int, y: int, aoi_json=None, gap_mask=None) -> bytes:
    """Display-only DEM heights; alpha is coverage, never filled with background data."""
    from osgeo import gdal
    gdal.UseExceptions()
    bounds = mercator_tile_bounds(z, x, y)
    source = gdal.Open(str(raster_file))
    if source is None:
        raise ValueError('Cannot read DEM')
    band = source.GetRasterBand(1)
    scale = band.GetScale() if band.GetScale() is not None else 1.0
    offset = band.GetOffset() if band.GetOffset() is not None else 0.0
    unit = (band.GetUnitType() or 'm').strip().lower()
    unit_scale = {'m': 1.0, 'meter': 1.0, 'metre': 1.0, 'meters': 1.0, 'metres': 1.0,
                  'ft': 0.3048, 'foot': 0.3048, 'feet': 0.3048, 'us survey foot': 1200 / 3937}.get(unit)
    if unit_scale is None:
        raise ValueError(f'Unsupported DEM display height unit: {unit}')
    first = gdal.Translate('', source, format='VRT', bandList=[1])
    warped = gdal.Warp('', first, format='MEM', dstSRS='EPSG:3857', outputBounds=bounds,
        width=256, height=256, resampleAlg='bilinear', outputType=gdal.GDT_Float32,
        dstNodata=-32768, dstAlpha=True, multithread=False, warpOptions=['NUM_THREADS=1'])
    if warped is None:
        raise ValueError('Cannot project DEM for terrain display')
    elevation = warped.GetRasterBand(1).ReadAsArray()
    alpha = warped.GetRasterBand(2).ReadAsArray() > 0
    if elevation is None or elevation.shape != (256, 256):
        raise ValueError('Incomplete terrain tile')
    alpha &= np.isfinite(elevation) & (elevation != -32768)
    if aoi_json:
        with tempfile.TemporaryDirectory(prefix='zeus-terrain-') as directory:
            cutline = Path(directory) / 'aoi.geojson'
            cutline.write_text(json.dumps({'type':'FeatureCollection','features':[
                {'type':'Feature','properties':{},'geometry':json.loads(aoi_json)}]}), encoding='utf-8')
            alpha &= _display_aoi_alpha(cutline, bounds) > 0
    if gap_mask:
        from .dataset_fetch.research.preview import validity_alpha
        alpha &= validity_alpha(gap_mask, bounds) > 0
    elevation = (elevation * scale + offset) * unit_scale
    elevation[~alpha] = np.nan
    rgba = encode_mapbox_terrain(elevation, -32768)
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode='RGBA').save(buffer, format='PNG', compress_level=6)
    return buffer.getvalue()

@lru_cache(maxsize=256)
def _cached_raster_tile(path: str, z: int, x: int, y: int, mtime: float, aoi_json=None, gap_mask=None, categorical=None) -> bytes:
    return render_raster_tile(Path(path), z, x, y, aoi_json, gap_mask, categorical)

@lru_cache(maxsize=256)
def _cached_terrain_tile(path: str, z: int, x: int, y: int, mtime: float, aoi_json=None, gap_mask=None) -> bytes:
    return render_terrain_tile(Path(path), z, x, y, aoi_json, gap_mask)

def _find_raster_file(project_path: Path, layer: str) -> Optional[Path]:
    """
    Find a raster file by layer name (display name or filename-based name).
    Checks processed/ first, then legacy location.
    """
    import re
    from .dataset_fetch.research.readers import resolve_active
    active = resolve_active(project_path, layer, 'raster')
    if active:
        return active
    rasters_processed_dir = project_path / 'data' / 'rasters' / 'processed'
    rasters_dir = project_path / 'data' / 'rasters'
    if rasters_processed_dir.exists():
        candidate = rasters_processed_dir / f'{layer}.tif'
        if candidate.exists():
            return candidate
        for item in rasters_processed_dir.iterdir():
            if item.suffix == '.tif':
                metadata_file = item.with_name(f'{item.name}.json')
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                        raw_name = item.stem
                        fallback = re.sub('_epsg\\d+_processed$', '', raw_name, flags=re.IGNORECASE)
                        fallback = re.sub('_processed$', '', fallback, flags=re.IGNORECASE)
                        display_name = _build_display_name_from_metadata(metadata, fallback)
                        if display_name == layer:
                            return item
                    except Exception:
                        pass
                raw_name = item.stem
                fallback_name = re.sub('_epsg\\d+_processed$', '', raw_name, flags=re.IGNORECASE)
                fallback_name = re.sub('_processed$', '', fallback_name, flags=re.IGNORECASE)
                if fallback_name == layer:
                    return item
    legacy_file = rasters_dir / f'{layer}.tif'
    if legacy_file.exists():
        return legacy_file
    return None

@router.get('/tiles/{project}/{layer}/{z}/{x}/{y}.png')
def get_raster_tile(project: str, layer: str, z: int, x: int, y: int):
    """
    Serve map tiles for raster datasets.

    Tiles are rendered on the fly in Web Mercator to align with MapLibre.
    Looks in data/rasters/processed/ first (canonical), then legacy location.
    """
    project_path = get_project_path_or_404(project)
    raster_file = _find_raster_file(project_path, layer)
    if raster_file is None or not raster_file.exists():
        raise HTTPException(status_code=404, detail=f"Raster layer '{layer}' not found in project '{project}'")
    try:
        mtime, mtime_ns = _dataset_mtime(raster_file)
        from .dataset_fetch.research.preview import preview_context
        preview=preview_context(project_path,raster_file)
        if preview:
            from .dataset_fetch.research.contracts import digest
            mtime=mtime_ns=int(digest({'preview':preview['identity'],'display_policy':RASTER_DISPLAY_POLICY}),16)
        cache_path = _tile_cache_path('rasters', project, layer, mtime_ns, z, x, y)
        cached_tile = _read_cache_file(cache_path)
        if cached_tile is not None:
            return Response(content=cached_tile, media_type='image/png')
        tile_bytes = _cached_raster_tile(str(raster_file), z, x, y, mtime, preview['aoi_json'] if preview else None,
            preview['gap_mask'] if preview else None,preview['categorical'] if preview else None)
        try:
            _write_cache_file(cache_path, tile_bytes)
        except Exception:
            pass
        return Response(content=tile_bytes, media_type='image/png')
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Failed to render raster tile: {exc}')

@router.get('/vector-tiles/{project}/{layer}/{z}/{x}/{y}.pbf')
def get_vector_tile(project: str, layer: str, z: int, x: int, y: int):
    """
    Serve Mapbox Vector Tiles (MVT) for a vector dataset.

    This is the preferred path for very large vector layers (e.g., NHN waterways) since loading
    full GeoJSON into MapLibre is not feasible.
    """
    project_path = get_project_path_or_404(project)
    vector_file = _find_vector_file(project_path, layer)
    if vector_file is None or not vector_file.exists():
        raise HTTPException(status_code=404, detail=f"Vector layer '{layer}' not found in project '{project}'")
    try:
        _, mtime_ns = _dataset_mtime(vector_file)
        _ensure_vector_tileset(project, layer, vector_file, mtime_ns)
        tile_path = _vector_tile_path(project, layer, mtime_ns, z, x, y)
        if not tile_path.exists():
            return Response(status_code=204, content=b'')
        with open(tile_path, 'rb') as handle:
            tile_bytes = handle.read()
        import hashlib
        base = _vector_tileset_dir(project,layer,mtime_ns)
        index = json.loads((base/'.complete').read_text())
        if hashlib.sha256(tile_bytes).hexdigest() != index['tiles'].get(tile_path.relative_to(base).as_posix()):
            (base/'.complete').unlink(missing_ok=True)
            raise ValueError('Vector tile cache integrity failure; the next request will rebuild it')
        return Response(content=tile_bytes, media_type='application/vnd.mapbox-vector-tile', headers={'Content-Encoding': 'gzip'})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Failed to render vector tile: {exc}')

@router.get('/terrain/{project}/{layer}/{z}/{x}/{y}.png')
def get_terrain_tile(project: str, layer: str, z: int, x: int, y: int):
    """
    Serve terrain tiles encoded as Mapbox Terrain-RGB for DEM layers.
    Looks in data/rasters/processed/ first (canonical), then legacy location.
    """
    project_path = get_project_path_or_404(project)
    raster_file = _find_raster_file(project_path, layer)
    if raster_file is None or not raster_file.exists():
        raise HTTPException(status_code=404, detail=f"Raster layer '{layer}' not found in project '{project}'")
    try:
        mtime, mtime_ns = _dataset_mtime(raster_file)
        from .dataset_fetch.research.preview import preview_context
        preview = preview_context(project_path, raster_file)
        if preview:
            mtime = mtime_ns = preview['identity']
        cache_path = _tile_cache_path('terrain-native-v2', project, layer, mtime_ns, z, x, y)
        cached_tile = _read_cache_file(cache_path)
        if cached_tile is not None:
            return Response(content=cached_tile, media_type='image/png')
        tile_bytes = _cached_terrain_tile(str(raster_file), z, x, y, mtime,
            preview['aoi_json'] if preview else None, preview['gap_mask'] if preview else None)
        try:
            _write_cache_file(cache_path, tile_bytes)
        except Exception:
            pass
        return Response(content=tile_bytes, media_type='image/png')
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Failed to render terrain tile: {exc}')

@router.get('/data/{project}/aoi/{filename}')
async def get_aoi_file(project: str, filename: str):
    """
    Get AOI files like start/end points in KMZ/KML format.
    """
    project_path = get_project_path_or_404(project)
    aoi_dir = project_path / 'aoi'
    file_path = aoi_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found in AOI folder of project '{project}'")
    suffix = file_path.suffix.lower()
    media_type = 'application/octet-stream'
    if suffix == '.kml':
        media_type = 'application/vnd.google-earth.kml+xml'
    elif suffix == '.kmz':
        media_type = 'application/vnd.google-earth.kmz'
    elif suffix == '.json' or suffix == '.geojson':
        media_type = 'application/json'
    with open(file_path, 'rb') as f:
        content = f.read()
    return Response(content=content, media_type=media_type)

@router.delete('/data/cache')
async def clear_cache():
    """
    Clear the GeoJSON conversion cache and on-disk tile caches.
    """
    global GEOJSON_CACHE
    cache_size = len(GEOJSON_CACHE)
    prefix = str(get_cloud_project_root()) if get_cloud_project_root() else None
    GEOJSON_CACHE = {key: value for key, value in GEOJSON_CACHE.items() if prefix and not key.startswith(prefix)}
    disk_entries_removed = 0
    if tile_cache_root().exists():
        try:
            disk_entries_removed = sum((1 for _ in tile_cache_root().iterdir()))
        except Exception:
            disk_entries_removed = 0
        shutil.rmtree(tile_cache_root(), ignore_errors=True)
    _ensure_cache_root()
    return {'message': f'Cache cleared ({cache_size} memory entries, {disk_entries_removed} disk namespaces)', 'status': 'success'}
