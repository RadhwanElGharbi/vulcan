"""Public raster downloads using GDAL, without the legacy ZEUS executable.

Raw outputs are AOI bounding-box subsets in the source CRS; processing then
clips to the polygon and reprojects to the project's chosen CRS.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from .constants import GDALWARP_BIN, SOIL_DEFAULT_DEPTH
from .job_state import _log_to_job, _run_command


def validate_source(category, override):
    text = (override or '').lower().strip()
    if not text or text == 'auto':
        return
    keywords = {
        'landcover': ('worldcover', 'world cover'),
        'soil': ('soilgrids', 'isric'),
        'geohazard': ('gem',),
        'roads': ('osm', 'openstreetmap', 'open street map'),
        'railways': ('osm', 'openstreetmap', 'open street map'),
        'powerlines': ('osm', 'openstreetmap', 'open street map'),
        'waterways': ('osm', 'openstreetmap', 'open street map', 'nhn', 'national hydro network', 'national hydrographic network'),
        'pipelines': ('osm', 'openstreetmap', 'open street map', 'cer', 'canada energy regulator', 'canadian energy regulator'),
        'protected_areas': ('cpcad', 'canadian protected and conserved'),
        'indigenous_lands': ('clss',),
    }
    allowed = keywords.get(category)
    if allowed and not any(keyword in text for keyword in allowed):
        raise ValueError(f"'{override}' has no download adapter for {category} in this local build. Use Auto or a supported source.")


def _subset(sources, ctx, raw_path, job, *, resolution=None, nodata=None):
    cmd = [GDALWARP_BIN, '--config', 'GDAL_HTTP_CONNECTTIMEOUT', '30',
           '--config', 'GDAL_HTTP_TIMEOUT', '180', '--config', 'GDAL_HTTP_MAX_RETRY', '2',
           '--config', 'GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR',
           '-overwrite', '-of', 'GTiff', '-co', 'COMPRESS=LZW', '-co', 'TILED=YES',
           '-te_srs', 'EPSG:4326', '-te', *map(str, ctx.bbox), '-r', 'near']
    if resolution:
        cmd.extend(['-tr', str(resolution), str(resolution), '-tap'])
    if nodata is not None:
        cmd.extend(['-dstnodata', str(nodata)])
    cmd.extend([*sources, str(raw_path)])
    _run_command(cmd, ctx.project_path, job, ctx, 'Download raster subset')
    return cmd


def worldcover_fetch(ctx, raw_path, job, override=None):
    text = (override or '').lower()
    year_match = re.search(r'20\d{2}', text)
    year = year_match.group() if year_match else '2021'
    if year not in ('2020', '2021') or 'dynamic' in text:
        raise RuntimeError('This local build supports ESA WorldCover 2020 and 2021. Select one of those sources.')
    version = 'v200' if year == '2021' else 'v100'
    west, south, east, north = ctx.bbox
    urls = []
    for lat in range(math.floor(south / 3) * 3, math.ceil(north / 3) * 3, 3):
        for lon in range(math.floor(west / 3) * 3, math.ceil(east / 3) * 3, 3):
            tile = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
            urls.append(f'https://esa-worldcover.s3.eu-central-1.amazonaws.com/{version}/{year}/map/ESA_WorldCover_10m_{year}_{version}_{tile}_Map.tif')
    _log_to_job(job, ctx, f'Fetching ESA WorldCover {year}: {len(urls)} source tile(s)')
    cmd = _subset(['/vsicurl/' + url for url in urls], ctx, raw_path, job, resolution=1/12000, nodata=0)
    return cmd, {'landcover_dataset': 'esa_worldcover', 'coverage_date': year,
                 'source_urls': urls, 'source_version': version, 'raw_is_bbox_subset': True}


def soilgrids_fetch(ctx, raw_path, job, override=None):
    text = (override or '').lower()
    prop = 'clay' if 'clay' in text else 'sand' if 'sand' in text else 'soc'
    depth = SOIL_DEFAULT_DEPTH
    url = f'https://files.isric.org/soilgrids/latest/data/{prop}/{prop}_{depth}_mean.vrt'
    _log_to_job(job, ctx, f'Fetching SoilGrids {prop}, depth {depth}, mean prediction')
    cmd = _subset(['/vsicurl/' + url], ctx, raw_path, job, resolution=250)
    return cmd, {'source_urls': [url], 'soil_property': prop, 'soil_depth': depth,
                 'soil_statistic': 'mean', 'raw_is_bbox_subset': True}


def seismic_fetch(ctx, raw_path, job, override=None):
    if 'sa1' in (override or '').lower() or '1.0' in (override or ''):
        raise RuntimeError('The public GEM source supports PGA with a 475-year return period; SA1.0 is unavailable in this build.')
    url = 'https://zenodo.org/records/8409647/files/GEM-GSHM_PGA-475y-rock_v2023.zip'
    archive = raw_path.parent / 'gem-source.zip'
    cmd = ['curl', '-sSfL', '--connect-timeout', '30', '--max-time', '600', '--retry', '2', url, '-o', str(archive)]
    _run_command(cmd, ctx.project_path, job, ctx, 'Download GEM seismic raster')
    import zipfile
    with zipfile.ZipFile(archive) as source:
        members = [name for name in source.namelist() if name.lower().endswith('.tif') and '475' in name]
        if len(members) != 1:
            raise RuntimeError('GEM archive did not contain the expected PGA raster.')
    raster = '/vsizip/' + archive.as_posix() + '/' + members[0]
    _subset([raster], ctx, raw_path, job, resolution=0.05)
    archive.unlink()
    return cmd, {'source_urls': [url], 'seismic_product': 'pga', 'return_period_years': 475,
                 'source_version': '2023.1', 'raw_is_bbox_subset': True}
