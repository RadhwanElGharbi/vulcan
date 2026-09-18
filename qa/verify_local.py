"""Integration checks for the running local review workspace (no downloads)."""
import io
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
runtime = Path(sys.executable).parent
if os.name == 'nt' and (runtime / 'Library/bin').exists():
    os.environ['PATH'] = str(runtime / 'Library/bin') + os.pathsep + os.environ['PATH']
    os.environ.setdefault('GDAL_DATA', str(runtime / 'Library/share/gdal'))
    os.environ.setdefault('PROJ_DATA', str(runtime / 'Library/share/proj'))
    _dlls = os.add_dll_directory(str(runtime / 'Library/bin'))

import requests
from osgeo import gdal, ogr
from PIL import Image
from shapely.geometry import shape
from shapely.ops import unary_union

gdal.UseExceptions()
BASE = 'http://127.0.0.1:3001/api'
PROJECT = 'Waterloo-Preview'

def get(path):
    response = requests.get(BASE + path, timeout=60)
    response.raise_for_status()
    return response

assert all(v == 'available' for v in get('/health').json()['services'].values())
schema = get('/openapi.json').json()
assert not any(term in path.lower() for path in schema['paths'] for term in ['pirl', 'auth', 'login', 'sorties', 'engineering'])
inventory = get(f'/projects/{PROJECT}/datasets').json()
coverage = get(f'/projects/{PROJECT}/dataset-coverage').json()
assert coverage['iso3'] == 'CAN', coverage['iso3']
assert len(inventory['rasters']) == 4, inventory
assert len(inventory['vectors']) == 2, inventory
aoi = unary_union([shape(f['geometry']) for f in get(f'/data/{PROJECT}/aoi/aoi.geojson').json()['features']])
report = {'project': PROJECT, 'checks': [], 'rasters': [], 'vectors': []}

for item in inventory['rasters']:
    path = Path(item['path'])
    if not path.is_absolute():
        path = ROOT / 'Projects' / PROJECT / path
    dataset = gdal.Open(str(path))
    assert dataset.GetSpatialRef().GetAuthorityCode(None) == '32617'
    band = dataset.GetRasterBand(1)
    stats = band.GetStatistics(False, True)
    assert band.GetMetadata().get('STATISTICS_VALID_PERCENT', '0') != '0'
    metadata = json.loads(path.with_suffix(path.suffix + '.json').read_text(encoding='utf-8'))
    assert metadata.get('validation_status') in ['passed', 'passed_with_warnings']
    report['rasters'].append({'name': item['name'], 'dimensions': [dataset.RasterXSize, dataset.RasterYSize], 'min': stats[0], 'max': stats[1], 'validation': metadata['validation_status']})
    dataset = None

for item in inventory['vectors']:
    data = get(f'/data/{PROJECT}/vectors/{item["name"]}').json()
    assert len(data['features']) > 0
    if item['name'].startswith('roads_'):
        assert any('highway' in f.get('properties', {}) for f in data['features'])
        for feature in data['features']:
            assert aoi.buffer(1e-5).covers(shape(feature['geometry']))
    report['vectors'].append({'name': item['name'], 'features': len(data['features'])})

z = 14
lon, lat = -80.54, 43.4725
x = int((lon + 180) / 360 * 2**z)
y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**z)
for item in inventory['rasters']:
    response = get(f'/tiles/{PROJECT}/{item["name"]}/{z}/{x}/{y}.png')
    assert response.headers['Content-Type'].startswith('image/png')
    tile = Image.open(io.BytesIO(response.content)).convert('RGBA')
    assert tile.size == (256, 256)
    assert tile.getchannel('A').getextrema()[1] > 0
dem = next(item for item in inventory['rasters'] if item['name'].startswith('dem_'))
assert get(f'/terrain/{PROJECT}/{dem["name"]}/{z}/{x}/{y}.png').headers['Content-Type'].startswith('image/png')
report['checks'].append('Vector attributes, AOI clipping, raster CRS/statistics, map tiles and DEM terrain tiles')

duplicate = requests.post(BASE + '/projects/create', data={'project_name': PROJECT, 'organization': 'QA'}, timeout=15)
assert duplicate.status_code == 409
invalid = requests.post(BASE + '/projects/create', data={'project_name': 'Invalid-AOI-QA', 'organization': 'QA', 'drawn_geojson': json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{'type':'Point','coordinates':[lon,lat]}}]})}, timeout=15)
assert invalid.status_code == 400
assert not (ROOT / 'Projects/Invalid-AOI-QA').exists()
assert requests.put(BASE + f'/projects/{PROJECT}/crs', json={'epsg':32618}, timeout=15).status_code == 409
report['checks'].append('Duplicate names and nonpolygon AOIs rejected; populated-project CRS protected')
report['checks'].append('No login, PIRL, drone-planning or engineering endpoints')
unsupported = requests.post(BASE + f'/projects/{PROJECT}/dataset-fetch', json={'categories':['soil'],'overrides':{'soil':'Unimplemented test source'}}, timeout=15)
assert unsupported.status_code == 400
report['checks'].append('Canadian source catalogue resolved; unsupported sources rejected before download')
output = ROOT / 'qa/verification-results.json'
output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, indent=2))
