import ast, json
from pathlib import Path
from types import SimpleNamespace
root=Path('apps/api/api/dataset_fetch')
def load_functions(filename, names, namespace):
    tree=ast.parse((root/filename).read_text(encoding='utf-8-sig'))
    body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]
    body += [node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
    exec(compile(ast.fix_missing_locations(ast.Module(body=body,type_ignores=[])),str(root/filename),'exec'),namespace)
ns={}
load_functions('utils.py',{'_status_from_issues'},ns)
ns.update(_gdal_info=lambda p:{},_extract_epsg_from_info=lambda i:'EPSG:4326',_extract_raster_statistics=lambda i:{'min':1,'max':2,'valid_percent':1},_ogr_info=lambda p:{'layers':[{}]},_vector_epsg=lambda p:None,_vector_feature_count=lambda i:10)
ns['_gdal_info']=lambda p:{'driverShortName':'GTiff'}
load_functions('validation.py',{'_validate_raster_file','_validate_vector_file'},ns)
class FixturePath:
    name='fixture.tif'
    def exists(self):return True
    def stat(self):return SimpleNamespace(st_size=2048)
ctx=SimpleNamespace(bbox=(-81,43,-80,44))
result={'method':'isolated production functions with mocked GDAL/OGR metadata; no network or project writes','raster_1_percent_valid':ns['_validate_raster_file'](FixturePath(),ctx),'vector_unknown_crs':ns['_validate_vector_file'](FixturePath(),ctx,expect_epsg='EPSG:32617')}
load_functions('portable_fetchers.py',{'validate_source'},ns)
for category,override in [('dem','unsupported source'),('landcover','ESA WorldCover 2025')]:
    try:ns['validate_source'](category,override);result[category+'_source_preflight']='accepted'
    except ValueError as e:result[category+'_source_preflight']=str(e)
assert result['raster_1_percent_valid'][0]=='passed'
assert result['vector_unknown_crs'][0]=='passed_with_warnings'
Path('tests/fetch-audit-probes.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
