"""Real GDAL fixtures plus adversarial protocol tests; no external downloads."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from api.dataset_fetch.research.contracts import Asset, ExecuteRequest, FetchPlan, Finding, PlanRequest, PlannedSelection, canonical, digest, file_hash, validate_acknowledgements
from api.dataset_fetch.research.registry import parameters, registry
from api.dataset_fetch.research.store import Store
from api.dataset_fetch.research.validation import validate_raster, validate_vector
from api.dataset_fetch.research.transport import Transport


AOI = {"type": "Polygon", "coordinates": [[[0,0],[10,0],[10,10],[0,10],[0,0]]]}


def raster(path, data, nodata=-9999, crs=True):
    from osgeo import gdal, osr
    values = np.asarray(data, dtype=np.float32)
    if values.ndim == 2:
        values = values[np.newaxis]
    ds = gdal.GetDriverByName("GTiff").Create(str(path), values.shape[2], values.shape[1], values.shape[0], gdal.GDT_Float32)
    ds.SetGeoTransform((0, 1, 0, 10, 0, -1))
    if crs:
        ref = osr.SpatialReference()
        ref.ImportFromEPSG(4326)
        ds.SetProjection(ref.ExportToWkt())
    for i, band in enumerate(values, 1):
        ds.GetRasterBand(i).SetNoDataValue(nodata)
        ds.GetRasterBand(i).WriteArray(band)
    ds = None
    return path


def test_one_percent_valid_requires_approval(tmp_path):
    values = np.full((10,10), -9999.)
    values[5,5] = 4
    path = raster(tmp_path / "one.tif", values)
    result, findings = validate_raster(path, AOI, registry()["copernicus-glo30"], selection_id="dem", gap_path=tmp_path/"gaps.tif")
    assert result["bands"][0]["valid_fraction"] == pytest.approx(.01)
    assert any(f.rule == "aoi-valid-coverage" and f.severity == "acknowledgement" for f in findings)


def test_all_bands_scanned_and_nan_blocked_as_empty(tmp_path):
    data = np.stack([np.ones((10,10)), np.full((10,10), np.nan)])
    _, findings = validate_raster(raster(tmp_path/"bands.tif", data), AOI, registry()["copernicus-glo30"], selection_id="dem")
    assert any(f.rule == "nonempty-raster" and "band2" in f.id for f in findings)
    assert any(f.rule=='finite-scientific-values' and f.severity=='block' for f in findings)


def test_declared_nan_nodata_is_a_gap_but_unmasked_infinity_blocks(tmp_path):
    values=np.ones((10,10));values[5,5]=np.nan
    _,findings=validate_raster(raster(tmp_path/'declared-nodata.tif',values,nodata=float('nan')),AOI,registry()['copernicus-glo30'],selection_id='dem')
    assert any(f.rule=='aoi-valid-coverage' for f in findings)
    assert not any(f.severity=='block' for f in findings)
    values[5,5]=np.inf
    _,findings=validate_raster(raster(tmp_path/'unmasked-inf.tif',values),AOI,registry()['copernicus-glo30'],selection_id='dem')
    assert any(f.rule=='finite-scientific-values' and f.severity=='block' for f in findings)


def test_hole_is_not_a_gap(tmp_path):
    polygon = {**AOI, "coordinates": AOI["coordinates"]+[[[4,4],[4,6],[6,6],[6,4],[4,4]]]}
    data = np.ones((10,10)); data[4:6,4:6] = -9999
    result, findings = validate_raster(raster(tmp_path/"hole.tif",data), polygon, registry()["copernicus-glo30"], selection_id="dem")
    assert result["bands"][0]["valid_fraction"] == pytest.approx(1)
    assert not findings


@pytest.mark.parametrize('pid',['io-lulc-2020-10class','io-lulc-annual-v1','io-lulc-annual-v2'])
def test_annual_landcover_exact_year_release_legend_and_complete_inventory(pid):
    import copy
    from api.dataset_fetch.research.stac_landcover import discover
    product=registry()[pid]
    collection=product.semantics['collection']
    legend=[{'values':[int(code)],'summary':label} for code,label in {'0':'No Data',**product.semantics['classes']}.items()]
    def item(year,tile='31N'):
        return {'type':'Feature','id':f'{tile}-{year}','collection':collection,'geometry':AOI,
            'properties':{'start_datetime':f'{year}-01-01T00:00:00Z','end_datetime':f'{year+1}-01-01T00:00:00Z',
                'proj:epsg':32631,'proj:shape':[10,10],'proj:transform':[10,0,500000,0,-10,100]},
            'assets':{'data':{'href':product.semantics['asset_prefix']+f'{tile}_{year}0101-{year+1}0101.tif','file:size':123,
                'raster:bands':[{'nodata':0,'spatial_resolution':10}],'file:values':copy.deepcopy(legend)}}}
    past,current=item(2019),item(2020)
    records=[]
    class Client:
        fault=None
        def json(self,url,**kwargs):
            records.append(url)
            if '/collections/' in url:
                result={'type':'Collection','id':collection,'license':'CC-BY-4.0','item_assets':{'data':{'file:values':legend}}}
            else:
                result={'type':'FeatureCollection','numberMatched':2,'numberReturned':1,'features':[copy.deepcopy(current if url.endswith('?next') else past)],
                    'links':[] if url.endswith('?next') else [{'rel':'next','href':product.endpoint+'/search?next'}]}
                if self.fault=='missing':result['numberMatched']=3
                if self.fault=='legend' and url.endswith('?next'):result['features'][0]['assets']['data']['file:values'][1]['summary']='Invented meaning'
                if self.fault=='release' and url.endswith('?next'):result['features'][0]['collection']='other-model'
                if self.fault=='grid' and url.endswith('?next'):result['features'][0]['properties']['proj:transform'][0]=20
            return result,{'retained':url}
        def signed_url(self,url):return url
        def inspect(self,asset,**kwargs):return asset.model_copy(update={'size':123})
    client=Client()
    assets,snapshots,findings=discover(product,{'year':'2020'},AOI,client)
    assert [asset.id for asset in assets]==['31N-2020'] and len(snapshots)==3 and not findings
    assert product.observation_period['reference_year']==2020
    assert assets[0].metadata['expected_native_grid']['datatype']=='Byte'
    for fault,match in [('missing','Incomplete'),('legend','legend contradicts'),('release','another model'),('grid','10 m grid')]:
        client.fault=fault
        with pytest.raises(ValueError,match=match):discover(product,{'year':'2020'},AOI,client)


def test_landcover_clouds_are_counted_separately_from_invalid_classes(tmp_path):
    product=registry()['io-lulc-annual-v2']
    values=np.ones((10,10));values[:2]=10
    result,findings=validate_raster(raster(tmp_path/'clouds.tif',values),AOI,product,selection_id='annual')
    assert result['bands'][0]['valid_fraction']==1
    assert result['bands'][0]['class_coverage']['10']['aoi_fraction']==pytest.approx(.2)
    assert len(findings)==1 and findings[0].rule=='landcover-non-surface-class' and findings[0].severity=='acknowledgement'
    values[2,2]=3  # Grass is an old-model code, not a V2 class.
    _,findings=validate_raster(raster(tmp_path/'wrong-legend.tif',values),AOI,product,selection_id='annual')
    assert any(f.severity=='block' and f.rule=='documented-value-domain' for f in findings)


def os_grid_fixture():
    folder=ROOT/'tests/fixtures/os_terrain'
    files={name:(folder/name).read_bytes() for name in ('TQ28.gml','TQ28.prj','Metadata_TQ28.xml','TQ28.asc.aux.xml')}
    files['TQ28.asc']=('ncols 200\nnrows 200\nxllcorner 520000\nyllcorner 180000\ncellsize 50\n'+((' '.join(['12.1']*200)+'\n')*200)).encode('ascii')
    return files


def os_grid_zip(files):
    import io,zipfile
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(files.items()):archive.writestr(name,raw)
    return out.getvalue()


def hwsd_tables():
    from api.dataset_fetch.research.hwsd import DEPTHS
    fields=['HWSD2_SMU_ID','SEQUENCE','LAYER','TOPDEP','BOTDEP','SHARE','ORG_CARBON']
    rows=[[12,sequence,depth,top,bottom,share,float(np.float32(.431))] for sequence,share in [(1,70),(2,30)] for depth,(top,bottom) in DEPTHS.items()]
    return {'HWSD2_SMU':{'columns':['HWSD2_SMU_ID'],'rows':[[12]]},'HWSD2_LAYERS':{'columns':fields,'rows':rows}}


def test_hwsd_discovery_distinguishes_local_contract_from_provider_response(tmp_path):
    from api.dataset_fetch.research.hwsd import discover,DOC,SOURCES,TABLES
    class Client(Transport):
        def inspect(self,asset):return asset
    store=Store(tmp_path/'store')
    assets,snapshots,findings=discover(registry()['fao-hwsd2-native'],Client(store))
    assert len(assets)==2 and not findings
    receipt=snapshots[0]['receipt']
    assert receipt['source_url'].startswith('zeus:qualification/')
    proof=json.loads(store.verify_blob(receipt['sha256']).read_bytes())
    assert proof['origin']=='ZEUS-generated native-table verification contract' and proof['evidence_urls']==[DOC]
    assert proof['native_tables']==TABLES
    assert {asset.expected_hash for asset in assets}=={values[2] for values in SOURCES.values()}


def test_hwsd_canonical_axis_metadata_preserves_native_grid_and_rejects_wrong_crs(tmp_path):
    from osgeo import gdal
    from pyproj import CRS
    from api.dataset_fetch.research.hwsd import native_vrt
    path=raster(tmp_path/'native.tif',np.ones((10,10)))
    ds=gdal.Open(str(path),gdal.GA_Update);ds.SetProjection(CRS.from_user_input('OGC:CRS84').to_wkt());ds=None
    before=file_hash(path);target=tmp_path/'canonical.vrt'
    details=native_vrt(str(path),target)
    ds=gdal.Open(str(target))
    assert list(ds.GetGeoTransform())==details['original_affine']
    assert np.array_equal(ds.ReadAsArray(),np.ones((10,10)))
    ds=None
    assert file_hash(path)==before
    ds=gdal.Open(str(path),gdal.GA_Update);ds.SetProjection(CRS.from_epsg(3857).to_wkt());ds=None
    with pytest.raises(ValueError,match='WGS 84'):native_vrt(str(path),tmp_path/'wrong.vrt')


def test_hwsd_keeps_all_components_and_depths_without_value_aggregation():
    from api.dataset_fetch.research.hwsd import validate_links,typed_row_hash
    tables=hwsd_tables();before=canonical(tables)
    result=validate_links(tables,{12})
    assert result['components']==2 and result['depth_records']==14
    assert result['component_share_totals_percent']=={'12':100}
    assert canonical(tables)==before
    assert typed_row_hash([float(np.float32(.431))],['Single'])
    with pytest.raises(ValueError,match='binary32'):typed_row_hash([.431],['Single'])
    with pytest.raises(ValueError,match='invalid'):typed_row_hash([float('nan')],['Single'])
    assert typed_row_hash([False],['Boolean'])
    with pytest.raises(ValueError,match='Boolean'):typed_row_hash([0],['Boolean'])
    with pytest.raises(ValueError,match='invalid'):typed_row_hash([False],['Single'])
    tables['HWSD2_SMU']['rows'].append([3])
    tables['HWSD2_LAYERS']['rows'].extend([[3,*row[1:]] for row in list(tables['HWSD2_LAYERS']['rows'])])
    report=validate_links(tables,{3,12})
    assert digest(report)==digest(json.loads(canonical(report)))


@pytest.mark.parametrize('fault,match',[('missing_id','grid IDs'),('missing_depth','missing depths'),('duplicate','duplicated'),('wrong_bounds','depth bounds'),('changed_share','inconsistent shares')])
def test_hwsd_blocks_unreconciled_mapping_units_and_components(fault,match):
    from api.dataset_fetch.research.hwsd import validate_links
    tables=hwsd_tables();rows=tables['HWSD2_LAYERS']['rows']
    if fault=='missing_id':tables['HWSD2_SMU']['rows']=[]
    if fault=='missing_depth':rows.pop()
    if fault=='duplicate':rows.append(list(rows[0]))
    if fault=='wrong_bounds':rows[0][4]=21
    if fault=='changed_share':rows[0][5]=71
    with pytest.raises(ValueError,match=match):validate_links(tables,{12})


def test_os_terrain_native_values_crs_precision_and_separate_dates(tmp_path):
    import zipfile
    from osgeo import gdal
    from api.dataset_fetch.research.os_terrain import describe_tile,prepare,tile_bounds
    native=os_grid_zip(os_grid_fixture());details,_=describe_tile(native,'TQ28')
    assert details['flying_start']=='2024-07-29' and details['processing_date']=='2025-05-08'
    assert details['metadata_date'].startswith('2026-05-29') and details['vertical_reference']=='EPSG:5701'
    assert details['prj_normalization']['provider_scale']!=details['prj_normalization']['metadata_authority_scale']
    assert tile_bounds('TQ28')==[520000,180000,530000,190000]
    archive=tmp_path/'national.zip';member='data/tq/tq28_OST50GRID_20260529.zip'
    with zipfile.ZipFile(archive,'w') as package:package.writestr(member,native)
    entry=Asset(id='os-grid',url='https://example.org/grid.zip',filename='grid.zip',metadata={'archive_sha256':file_hash(archive),'tiles':[{'member':member,**details}]})
    work=tmp_path/'work';work.mkdir()
    sources=prepare(entry,str(archive),work,lambda:False)
    ds=gdal.Open(sources[0][1])
    assert ds.GetGeoTransform()==(520000,50,0,190000,0,-50) and ds.GetRasterBand(1).GetUnitType()=='m'
    assert np.all(ds.ReadAsArray()==np.float32(12.1)) and '27700' in ds.GetProjection()


@pytest.mark.parametrize('fault,match',[('dependency','inventory'),('truncated','row inventory'),('nonfinite','nonfinite'),('crs','PRJ scale'),('vertical','vertical reference')])
def test_os_terrain_rejects_uninterpretable_or_truncated_inputs(fault,match):
    from api.dataset_fetch.research.os_terrain import describe_tile
    files=os_grid_fixture()
    if fault=='dependency':del files['TQ28.gml']
    if fault=='truncated':files['TQ28.asc']=files['TQ28.asc'].rsplit(b'\n',2)[0]
    if fault=='nonfinite':files['TQ28.asc']=files['TQ28.asc'].replace(b'12.1',b'nan',1)
    if fault=='crs':files['TQ28.prj']=files['TQ28.prj'].replace(b'0.999601272',b'0.999')
    if fault=='vertical':files['Metadata_TQ28.xml']=files['Metadata_TQ28.xml'].replace(b'urn:ogc:def:crs:EPSG::5701',b'urn:ogc:def:crs:EPSG::27700')
    with pytest.raises(ValueError,match=match):describe_tile(os_grid_zip(files),'TQ28')


def test_usgs_s3_complete_pagination_and_truncated_page(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from api.dataset_fetch.research.usgs_projects import list_objects
    transport=Transport(Store(tmp_path/'store'));calls=[]
    @contextmanager
    def request(method,url,params):
        calls.append(params)
        page=len(calls)
        payload=f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Prefix>tiles/</Prefix><KeyCount>1</KeyCount><IsTruncated>{"true" if page==1 else "false"}</IsTruncated><NextContinuationToken>next</NextContinuationToken><Contents><Key>tiles/{page}.tif</Key><Size>12</Size><ETag>identity-{page}</ETag></Contents></ListBucketResult>'.encode()
        yield SimpleNamespace(headers={},payload=payload)
    monkeypatch.setattr(transport,'request',request)
    monkeypatch.setattr(transport,'chunks',lambda response:[response.payload])
    records=[];objects=list_objects('tiles/',transport,records)
    assert sorted(objects)==['tiles/1.tif','tiles/2.tif'] and len(records)==2
    assert calls[1]['continuation-token']=='next'
    @contextmanager
    def truncated(*args,**kwargs):
        yield SimpleNamespace(headers={},payload=b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Prefix>tiles/</Prefix><KeyCount>0</KeyCount><IsTruncated>true</IsTruncated></ListBucketResult>')
    monkeypatch.setattr(transport,'request',truncated)
    with pytest.raises(ValueError,match='truncated'):list_objects('tiles/',transport,[])


def test_usgs_project_metadata_identity_units_and_publication_time(tmp_path):
    import xml.etree.ElementTree as ET
    from api.dataset_fetch.research.usgs_projects import project_metadata
    root=ET.Element('metadata')
    fields={'idinfo/citation/citeinfo/title':'USGS 1 Meter 13 x47y444 CO_DRCOG_2020_B20',
        'idinfo/citation/citeinfo/pubdate':'20220216',
        'idinfo/descript/abstract':'All bare earth elevation values are in meters and referenced to the North American Vertical Datum of 1988 (NAVD88).',
        'spref/horizsys/planar/gridsys/gridsysn':'NAD83 / UTM zone 13N','spref/horizsys/planar/gridsys/utm/utmzone':'13',
        'idinfo/timeperd/timeinfo/rngdates/begdate':'20200526','idinfo/timeperd/timeinfo/rngdates/enddate':'20210313','idinfo/timeperd/current':'publication date',
        'spdoinfo/rastinfo/colcount':'10012','spdoinfo/rastinfo/rowcount':'10012'}
    fields.update({'idinfo/spdom/bounding/'+key:str(value) for key,value in zip(('westbc','southbc','eastbc','northbc'),(-105.35,40.02,-105.23,40.11))})
    for path,value in fields.items():
        node=root
        for part in path.split('/'):
            child=node.find(part)
            if child is None: child=ET.SubElement(node,part)
            node=child
        node.text=value
    path=tmp_path/'metadata.xml';ET.ElementTree(root).write(path)
    result=project_metadata(path,'USGS_1M_13_x47y444_CO_DRCOG_2020_B20')
    assert result['source_crs']=='EPSG:26913' and result['vertical_datum']=='NAVD88'
    assert result['provider_time_range']['currentness']=='publication date'
    assert result['expected_native_grid']['width']==10012
    with pytest.raises(ValueError,match='contradicts'):project_metadata(path,'USGS_1M_13_x48y444_CO_DRCOG_2020_B20')
    root.find('idinfo/descript/abstract').text='Units are unknown.';ET.ElementTree(root).write(path)
    with pytest.raises(ValueError,match='mandatory elevation units'):project_metadata(path,'USGS_1M_13_x47y444_CO_DRCOG_2020_B20')
    assert project_metadata(path,'USGS_1M_13_x47y444_CO_DRCOG_2020_B20',boxes=[[-80,40,-79,41]]) is None


@pytest.mark.parametrize('grid,datum,zone,expected',[
    ('Transverse_Mercator','','10','EPSG:26910'),
    ('universal transverse mercator','','10.0','EPSG:26910'),
    ('UTM','North American Datum of 1983','10','EPSG:26910'),
    ('EPSG:26910','','','EPSG:26910'),
    ('NAD83(2011) / UTM zone 10N','NAD83(2011)','10','EPSG:6339'),
    ('Transverse Mercator','NAD83(HARN)','10','EPSG:3740'),
])
def test_usgs_crs_accepts_equivalent_declarations_and_preserves_realizations(grid,datum,zone,expected):
    import xml.etree.ElementTree as ET
    from api.dataset_fetch.research.usgs_projects import project_crs
    root=ET.fromstring(f'<metadata><spref><horizsys><geodetic><horizdn>{datum}</horizdn></geodetic><planar><gridsys><gridsysn>{grid}</gridsysn><utm><utmzone>{zone}</utmzone><transmer><sfctrmer>0.9996</sfctrmer><longcm>-123</longcm><latprjo>0</latprjo><feast>500000</feast><fnorth>0</fnorth></transmer></utm></gridsys></planar></horizsys></spref></metadata>')
    result,_,evidence=project_crs(root,'North American Datum of 1983 (NAD83); survey 2011','fixture')
    assert result==expected and evidence['grid_declaration']==grid
    root.find('spref/horizsys/planar/gridsys/utm/transmer/longcm').text='-117'
    with pytest.raises(ValueError,match='contradicts'):project_crs(root,'NAD83','fixture')


@pytest.mark.parametrize('grid,datum,zone',[
    ('NAD83 / UTM zone 11N','','10'),
    ('EPSG:26910','WGS84','10'),
    ('EPSG:32610','','10'),
    ('Transverse_Mercator','Unknown datum','10'),
    ('Transverse_Mercator','','10.5'),
])
def test_usgs_crs_still_blocks_conflicting_or_unresolved_metadata(grid,datum,zone):
    import xml.etree.ElementTree as ET
    from api.dataset_fetch.research.usgs_projects import project_crs
    root=ET.fromstring(f'<metadata><spref><horizsys><geodetic><horizdn>{datum}</horizdn></geodetic><planar><gridsys><gridsysn>{grid}</gridsysn><utm><utmzone>{zone}</utmzone></utm></gridsys></planar></horizsys></spref></metadata>')
    with pytest.raises(ValueError,match='USGS tile fixture:'):project_crs(root,'NAD83','fixture')


def test_usgs_crs_does_not_discard_an_explicit_abstract_realization():
    import xml.etree.ElementTree as ET
    from api.dataset_fetch.research.usgs_projects import project_crs
    root=ET.fromstring('<metadata><spref><horizsys><planar><gridsys><gridsysn>Transverse_Mercator</gridsysn><utm><utmzone>10</utmzone></utm></gridsys></planar></horizsys></spref></metadata>')
    assert project_crs(root,'Coordinates are NAD83(2011)','fixture')[0]=='EPSG:6339'
    with pytest.raises(ValueError,match='realization'):project_crs(root,'Coordinates are NAD83(unknown)','fixture')


def test_usgs_project_inventory_cannot_accept_missing_ids(tmp_path):
    from api.dataset_fetch.research.usgs_projects import discover_projects
    class Incomplete:
        def json(self,url,params):
            result={'objectIdField':'OBJECTID'} if not url.endswith('/query') else {'count':2} if params.get('returnCountOnly') else {'objectIds':[1]}
            return result,{'retained':'fixture'}
    with pytest.raises(ValueError,match='inventory is incomplete'):
        discover_projects(registry()['usgs-3dep-1m'],[[-105.27,40.01,-105.267,40.013]],Incomplete())


def test_frozen_auxiliary_operations_do_not_reselect_and_reject_unknown_crs(monkeypatch):
    from api.dataset_fetch.research import projection
    aoi={'type':'Polygon','coordinates':[[[-.13,51.50],[-.127,51.50],[-.127,51.503],[-.13,51.503],[-.13,51.50]]]}
    recipe={'native_export':False,'target_crs':'EPSG:27700'}
    issues=projection.freeze_auxiliary_operations(registry()['os-openroads'],[],recipe,aoi)
    assert len(recipe['auxiliary_operations'])==2
    before=projection.auxiliary_transformer(recipe,4326,27700,aoi).transform(-.13,51.50)
    assert 529000<before[0]<531000 and 179000<before[1]<181000
    def forbidden(*args,**kwargs):raise AssertionError('Runtime tried to reselect a coordinate operation')
    monkeypatch.setattr(projection,'resolve_operation',forbidden)
    assert projection.auxiliary_transformer(recipe,4326,27700,aoi).transform(-.13,51.50)==before
    with pytest.raises(ValueError,match='confirmed plan'):projection.auxiliary_transformer(recipe,4326,3857,aoi)
    assert all(op['coordinate_order']=='traditional GIS x/y' for op in recipe['auxiliary_operations'])


def test_arcgis_source_schema_domains_and_missing_fields():
    from api.dataset_fetch.research.arcgis_contract import validate_features
    metadata={'geometryType':'esriGeometryPolyline','fields':[{'name':'OBJECTID','type':'esriFieldTypeOID','nullable':False},{'name':'STATUS','type':'esriFieldTypeString','domain':{'type':'codedValue','codedValues':[{'code':'active'}]}}]}
    payload={'features':[{'properties':{'OBJECTID':12,'STATUS':'active'},'geometry':{'type':'LineString','coordinates':[[0,0],[1,1]]}}]}
    validate_features(payload,metadata)
    payload['features'][0]['properties']['STATUS']='invented'
    with pytest.raises(ValueError,match='coded domain'):validate_features(payload,metadata)
    del payload['features'][0]['properties']['STATUS']
    with pytest.raises(ValueError,match='omitted a declared attribute'):validate_features(payload,metadata)
    payload['features'][0]['properties']={'OBJECTID':'12','STATUS':'active'}
    with pytest.raises(ValueError,match='integer attribute'):validate_features(payload,metadata)


def test_census_exact_layer_selection_and_identity():
    from api.dataset_fetch.research.discovery import discover
    template=registry()['census-aiannha-2026']
    for selected, expected in template.semantics['layers_by_parameter']['layers'].items():
        product=template.model_copy(deep=True)
        class Client:
            def json(self,url,params):
                assert url.removesuffix('/query').endswith('/'+str(expected['id']))
                if not url.endswith('/query'):
                    return {'name':expected['name'],'fields':[{'name':'OBJECTID','type':'esriFieldTypeOID'}], 'extent':{'spatialReference':{'wkid':4326}},'geometryType':'esriGeometryPolygon'},{}
                return ({'count':0} if params.get('returnCountOnly') else {'objectIds':None}),{}
        assets, discovery, _=discover(product,parameters(product,{'area_type':selected}),AOI,'2026-01-01T00:00:00Z',Client())
        assert assets==[] and product.semantics['selected_layer']==expected
        assert discovery[-1]['arcgis']['expected_ids']==[]
    class WrongLayer:
        def json(self,*args,**kwargs):return {'name':'Another geographic product'},{}
    with pytest.raises(ValueError,match='layer identity changed'):
        discover(template,parameters(template,{}),AOI,'2026-01-01T00:00:00Z',WrongLayer())
    with pytest.raises(ValueError):parameters(registry()['census-aiannha-2026'],{'area_type':'all'})


@pytest.mark.parametrize('identity',['nor-protected-areas','nor-proposed-protected-areas','eng-nnr','eng-lnr'])
def test_conservation_service_identity_is_mandatory(identity):
    from api.dataset_fetch.research.discovery import discover
    product=registry()[identity]
    class Client:
        def json(self,url,params):
            if url.endswith('/query'):return ({'count':0} if params.get('returnCountOnly') else {'objectIds':None}),{}
            return {'name':product.semantics['expected_layer_name'],'geometryType':'esriGeometryPolygon','fields':[{'name':'OBJECTID','type':'esriFieldTypeOID'}],'extent':{'spatialReference':{'wkid':4326}}},{}
    assert discover(product,{},AOI,'2026-01-01T00:00:00Z',Client())[0]==[]
    class Replaced:
        def json(self,*args,**kwargs):return {'name':'Different product'},{}
    with pytest.raises(ValueError,match='layer identity'):discover(registry()[identity],{},AOI,'2026-01-01T00:00:00Z',Replaced())


def test_imagery_exact_scene_ids_across_pages_and_missing_selection(tmp_path,monkeypatch):
    from api.dataset_fetch.research.discovery import discover
    from api.dataset_fetch.research import imagery
    store=Store(tmp_path/'store');client=Transport(store)
    retained=client.retain_bytes(b'fixture calibration response','https://example.org/metadata')
    monkeypatch.setattr(imagery,'calibration',lambda *args:{'B04':{'scale':.0001,'offset':-.1,'units':'1'}})
    class Catalogue:
        def __init__(self):self.store=store;self.selected=[];self.pages=[]
        def json(self,url,**kwargs):
            self.pages.append(url)
            key='first' if url.endswith('/search') else 'second'
            return {'type':'FeatureCollection','features':[{'id':key,'properties':{'datetime':'2026-01-01T10:00:00Z','s2:processing_baseline':'05.00'},'assets':{
                band:{'href':'https://example.org/'+key+'/'+band} for band in ['product-metadata','B04','SCL']}}],
                'links':[{'rel':'next','href':'https://example.org/next'}] if key=='first' else []},{}
        def download(self,asset,**kwargs):self.selected.append(asset.id);return retained
        def signed_url(self,url):return url
        def inspect(self,asset,**kwargs):return asset
    p=registry()['sentinel2-l2a'];client=Catalogue()
    params=parameters(p,{'start':'2026-01-01','end':'2026-01-01','scene_ids':['second'],'bands':['B04']})
    assets,_,_=discover(p,params,AOI,'2026-01-02T00:00:00Z',client)
    assert [a.id for a in assets]==['second:B04','second:SCL'] and len(client.pages)==2
    assert client.selected==['second:product-metadata']
    with pytest.raises(ValueError,match='no alternate scene'):
        discover(p,{**params,'scene_ids':['missing']},AOI,'2026-01-02T00:00:00Z',Catalogue())


def test_defaults_respect_declared_country_and_never_substitute_catalogue_aliases():
    from api.dataset_fetch.research.registry import public_registry
    usa=public_registry(['USA'])['default_product_by_category']
    canada=public_registry(['CAN'])['default_product_by_category']
    unknown=public_registry()['default_product_by_category']
    assert usa['indigenous_lands']=='census-aiannha-2026'
    assert canada['indigenous_lands']=='can-clss'
    assert 'indigenous_lands' not in unknown and 'protected_areas' not in usa
    assert usa['dem']==canada['dem']=='copernicus-glo30'


def test_cgiar_tile_edges_and_polar_range_do_not_reselect():
    from api.dataset_fetch.research.discovery import discover
    product=registry()['cgiar-srtm-4.1']
    class Client:
        def inspect(self,asset):return asset
    aoi={'type':'Polygon','coordinates':[[[50,20],[55,20],[55,25],[50,25],[50,20]]]}
    assets,_,_=discover(product,{},aoi,'2026-01-01T00:00:00Z',Client())
    assert [a.id for a in assets]==['srtm_47_08']
    assert assets[0].metadata['expected_native_grid']['origin']==[50,25]
    aoi={'type':'Polygon','coordinates':[[[50,65],[55,65],[55,70],[50,70],[50,65]]]}
    assets,_,findings=discover(product,{},aoi,'2026-01-01T00:00:00Z',Client())
    assert not assets and any(f.rule=='catalogue-coverage' for f in findings)


def test_original_project_aoi_is_retained_with_exact_operation(tmp_path,monkeypatch):
    from pyproj import Transformer
    from shapely.geometry import mapping, shape
    from shapely.ops import transform
    from api.dataset_fetch.research.aoi import read_aoi, preserve_aoi, replay_aoi
    geographic={'type':'Polygon','coordinates':[[[-80.55,43.47],[-80.54,43.47],[-80.54,43.48],[-80.55,43.48],[-80.55,43.47]],
        [[-80.548,43.472],[-80.548,43.474],[-80.546,43.474],[-80.546,43.472],[-80.548,43.472]]]}
    projected=transform(Transformer.from_crs(4326,32617,always_xy=True).transform,shape(geographic))
    path=tmp_path/'aoi.geojson'
    raw=canonical({'type':'FeatureCollection','crs':{'type':'name','properties':{'name':'EPSG:32617'}},'features':[{'type':'Feature','properties':{'source':'fixture'},'geometry':mapping(projected)}]})
    path.write_bytes(raw)
    aoi,evidence=read_aoi(path)
    assert shape(aoi).symmetric_difference(shape(geographic)).area<1e-12
    assert len(aoi['coordinates'])==2
    assert evidence['coordinate_operation']['coordinate_order']=='traditional GIS x/y'
    ctx=SimpleNamespace(cutline_path=path,aoi_provenance=evidence)
    store=Store(tmp_path/'store')
    receipts,_=preserve_aoi(ctx,Transport(store))
    assert store.verify_blob(receipts[0]['receipt']['sha256']).read_bytes()==raw
    assert evidence['normalized_aoi_hash']==digest(aoi)
    def forbidden(*args,**kwargs):raise AssertionError('AOI replay tried to reselect an operation')
    monkeypatch.setattr('api.dataset_fetch.research.aoi.resolve_operation',forbidden)
    selection=SimpleNamespace(recipe={'aoi_normalization':evidence},discovery=receipts)
    replay_aoi(selection,SimpleNamespace(aoi_hash=digest(aoi)),store)
    with pytest.raises(ValueError,match='confirmed geographic footprint'):
        replay_aoi(selection,SimpleNamespace(aoi_hash='wrong'),store)
    path.write_bytes(raw+b' ')
    with pytest.raises(ValueError,match='AOI input changed'):preserve_aoi(ctx,Transport(store))


def test_frozen_native_grid_tolerance_is_bounded(tmp_path):
    from osgeo import gdal
    from api.dataset_fetch.research.processing import run_selection
    from api.dataset_fetch.research.projection import freeze_raster_operations
    store=Store(tmp_path/'store')
    product=registry()['copernicus-glo30']
    for delta, succeeds in [(1e-8,True),(1e-5,False)]:
        path=raster(tmp_path/f'grid-{delta}.tif',np.ones((10,10)))
        ds=gdal.Open(str(path),gdal.GA_Update);ds.SetGeoTransform((0,1,0,10,0,-1-delta));ds=None
        receipt=Transport(store).retain_bytes(path.read_bytes(),'https://example.org/grid.tif').model_copy(update={'asset_id':'grid'})
        asset=Asset(id='grid',url=receipt.source_url,filename='grid.tif',metadata={'expected_native_grid':{'width':10,'height':10,'x_spacing':1,'y_spacing':1,'spacing_absolute_tolerance':1e-7}})
        recipe={'target_crs':'EPSG:4326','extent':[0,0,10,10],'spacing':[1,1],'resampling':'near','nodata':-9999,'native_export':False}
        freeze_raster_operations(product,[asset],recipe,AOI)
        selection=PlannedSelection(id='grid',product=product,parameters={},assets=[asset],recipe=recipe)
        if succeeds:
            _,results,_=run_selection(selection,[receipt],SimpleNamespace(aoi=AOI,target_crs='EPSG:4326'),store,tmp_path/f'out-{delta}')
            assert results[0]['source_registrations'][0]['affine_grid'][5]==-1-delta
        else:
            with pytest.raises(ValueError,match='frozen native grid contract'):
                run_selection(selection,[receipt],SimpleNamespace(aoi=AOI,target_crs='EPSG:4326'),store,tmp_path/f'out-{delta}')


def test_verified_http_cache_rechecks_source_and_rejects_corruption(tmp_path,monkeypatch):
    store=Store(tmp_path/'store');transport=Transport(store)
    receipt=transport.retain_bytes(b'exact-source','https://example.org/data.tif',{'etag':'"v1"','last-modified':'Thu, 17 Sep 2026 01:00:00 GMT'})
    store.cache_receipt(receipt)
    asset=Asset(id='new-plan-asset',url=receipt.source_url,filename='data.tif',size=receipt.size,etag=receipt.headers['etag'],last_modified=receipt.headers['last-modified'])
    calls=[]
    def inspect(value,**kwargs):calls.append(value.id);return value
    monkeypatch.setattr(transport,'inspect',inspect)
    reused=transport.download(asset)
    assert reused.sha256==receipt.sha256 and reused.asset_id==asset.id and calls==[asset.id]
    assert 'zeus-cache-revalidated-at' in reused.headers
    monkeypatch.setattr(transport,'inspect',lambda value,**kwargs:value.model_copy(update={'etag':'"v2"'}))
    with pytest.raises(ValueError,match='Source changed'):transport.download(asset)
    store.verify_blob(receipt.sha256).write_bytes(b'wrong-source')
    with pytest.raises(ValueError,match='corrupt'):transport.download(asset)


def test_optional_native_vector_measures_are_preserved_and_require_review(tmp_path):
    from osgeo import ogr,osr
    from api.dataset_fetch.research.processing import run_selection
    path=tmp_path/'measures.gpkg';dataset=ogr.GetDriverByName('GPKG').CreateDataSource(str(path))
    reference=osr.SpatialReference();reference.ImportFromEPSG(4326)
    layer=dataset.CreateLayer('lines',reference,ogr.wkbLineStringM)
    feature=ogr.Feature(layer.GetLayerDefn());feature.SetGeometry(ogr.CreateGeometryFromWkt('LINESTRING M (-1 5 10, 5 5 20, 11 5 30)'))
    layer.CreateFeature(feature);feature=None;layer=None;dataset=None
    store=Store(tmp_path/'store');receipt=Transport(store).retain_bytes(path.read_bytes(),'https://example.org/measures.gpkg').model_copy(update={'asset_id':'native'})
    selection=PlannedSelection(id='measured',product=registry()['hydrorivers-v1'],parameters={},assets=[Asset(id='native',url=receipt.source_url,filename='measures.gpkg')],recipe={})
    outputs,results,issues=run_selection(selection,[receipt],SimpleNamespace(aoi=AOI,target_crs='EPSG:4326'),store,tmp_path/'out')
    dataset=ogr.Open(str(tmp_path/'out'/outputs[0]['file']));feature=dataset.GetLayer(0).GetNextFeature()
    properties=json.loads(feature.GetField('source_properties'));record=properties['zeus_native_measured_geometry']
    native=ogr.CreateGeometryFromWkb(bytes.fromhex(record['wkb']))
    assert native.IsMeasured() and [native.GetM(i) for i in range(native.GetPointCount())]==[10,20,30]
    assert not feature.GetGeometryRef().IsMeasured() and results[0]['native_measured_features']==1
    assert any(f.rule=='optional-native-measures' and f.severity=='acknowledgement' for f in issues)
    feature=None;dataset=None


def test_hrdem_exact_acquisition_identifiers_do_not_select_an_alternative():
    from api.dataset_fetch.research.discovery import discover
    product=registry()['can-hrdem-dtm']
    selected=parameters(product,{'acquisition_ids':['survey-b']})
    class Catalogue:
        def json(self,url,params):
            return {'type':'FeatureCollection','features':[{'id':name,'geometry':AOI,'properties':{'datetime':'2020-01-01T00:00:00Z'},'assets':{'dtm':{'href':'https://example.org/'+name+'.tif'}}} for name in ['survey-a','survey-b']]},{'fixture':'retained catalogue'}
        def inspect(self,asset):return asset
    assets,_,_=discover(product,selected,AOI,'2026-01-01T00:00:00Z',Catalogue())
    assert len(assets)==1 and assets[0].metadata['item']=='survey-b'
    with pytest.raises(ValueError,match='no alternate acquisition'):discover(product,{'acquisition_ids':['absent']},AOI,'2026-01-01T00:00:00Z',Catalogue())
    with pytest.raises(ValueError,match='identifiers'):parameters(product,{'acquisition_ids':['https://unregistered.org/source.tif']})


def test_unknown_crs_is_not_waivable(tmp_path):
    with pytest.raises(ValueError, match="CRS"):
        validate_raster(raster(tmp_path/"bad.tif",np.ones((10,10)),crs=False), AOI, registry()["copernicus-glo30"], selection_id="dem")


def test_landcover_invalid_codes_block(tmp_path):
    data = np.full((10,10), 10); data[0,0] = 999
    _, findings = validate_raster(raster(tmp_path/"lc.tif",data), AOI, registry()["worldcover-2021"], selection_id="lc")
    assert any(f.rule == "documented-value-domain" and f.severity == "block" for f in findings)


def test_fractional_small_aoi(tmp_path):
    small = {"type": "Polygon", "coordinates": [[[4.1,4.1],[4.2,4.1],[4.2,4.2],[4.1,4.2],[4.1,4.1]]]}
    result, findings = validate_raster(raster(tmp_path/"small.tif",np.ones((10,10))), small, registry()["gem-pga-2023"], selection_id="hazard")
    assert result["bands"][0]["intersecting_cells"] == 1
    assert result["bands"][0]["valid_fraction"] == pytest.approx(1)
    assert not findings


def test_scientific_hash_ignores_container_metadata(tmp_path):
    from osgeo import gdal
    a = raster(tmp_path/"a.tif",np.ones((10,10)))
    b = raster(tmp_path/"b.tif",np.ones((10,10)))
    ds = gdal.Open(str(b), gdal.GA_Update); ds.SetMetadataItem("history", "different execution time"); ds = None
    p = registry()["copernicus-glo30"]
    assert file_hash(a) != file_hash(b)
    assert validate_raster(a,AOI,p,selection_id="a")[0]["scientific_hash"] == validate_raster(b,AOI,p,selection_id="a")[0]["scientific_hash"]


def test_acknowledgements_exact_and_cannot_override_block():
    f = Finding(id="one", rule="x", severity="acknowledgement", message="gap")
    with pytest.raises(ValueError): validate_acknowledgements([f], [])
    with pytest.raises(ValueError): validate_acknowledgements([f], ["one", "extra"])
    validate_acknowledgements([f], ["one"])
    with pytest.raises(ValueError): validate_acknowledgements([f.model_copy(update={"severity": "block"})], ["one"])


def test_no_free_text_parameter_dispatch():
    p = registry()["worldcover-2021"]
    with pytest.raises(ValueError): parameters(p, {"version": "2025"})
    with pytest.raises(ValueError): parameters(registry()["soilgrids-soc"], {"depth": "0-30cm"})


def test_soilgrids_property_units_depth_and_publisher_checksums():
    from api.dataset_fetch.research.soilgrids import checksums
    products=registry()
    assert len([key for key in products if key.startswith('soilgrids-')])==11
    assert products['soilgrids-nitrogen'].units=='cg/kg'
    assert products['soilgrids-bdod'].semantics['conversion']=='divide by 100 for kg/dm3'
    assert products['soilgrids-ocs'].units=='t/ha'
    assert parameters(products['soilgrids-ocs'],{})['depth']=='0-30cm'
    with pytest.raises(ValueError):parameters(products['soilgrids-ocs'],{'depth':'0-5cm'})
    encoded=('a'*64+'  native.tif\n').encode()
    assert checksums(encoded)=={'native.tif':'a'*64}
    for payload in (encoded+encoded,b'<html>error</html>',('a'*64+'  ../outside.tif').encode()):
        with pytest.raises(ValueError):checksums(payload)


def test_soilgrids_vrt_coarse_native_registration_and_external_dependencies(tmp_path):
    from api.dataset_fetch.research.soilgrids import vrt_contract,validate_tiles
    from pyproj import CRS
    from osgeo import gdal
    stem='soc_0-5cm_mean';crs=CRS.from_proj4('+proj=igh +datum=WGS84 +units=m +no_defs').to_wkt()
    xml=f'''<VRTDataset rasterXSize="1000" rasterYSize="1000"><SRS>{crs}</SRS><GeoTransform>0,250,0,250000,0,-250</GeoTransform>
      <VRTRasterBand dataType="Int16" band="1"><NoDataValue>-32768</NoDataValue><ComplexSource>
      <SourceFilename relativeToVRT="1">./{stem}/tileSG-010-049/tileSG-010-049_1-1.tif</SourceFilename><SourceBand>1</SourceBand>
      <SourceProperties RasterXSize="1" RasterYSize="2" DataType="Int16"/><SrcRect xOff="0" yOff="0" xSize="1" ySize="2"/>
      <DstRect xOff="10.5" yOff="20.5" xSize="62" ySize="124"/><NODATA>-32768</NODATA></ComplexSource></VRTRasterBand></VRTDataset>'''
    _,_,sources=vrt_contract(xml.encode(),stem)
    grid=sources[0]['grid'];assert grid['affine'][1]==15500 and grid['affine'][5]==-15500
    path=tmp_path/'native.tif';ds=gdal.GetDriverByName('GTiff').Create(str(path),1,2,1,gdal.GDT_Int16)
    ds.SetProjection(crs);ds.SetGeoTransform(grid['affine']);ds.GetRasterBand(1).SetNoDataValue(-32768);ds=None
    product=registry()['soilgrids-soc'];product.semantics['source_crs']=crs
    entry=Asset(id='fixture',url='https://example.org/native.tif',filename='native.tif',metadata={'soilgrids_native_grid':grid})
    validate_tiles([(entry,str(path))],product)
    ds=gdal.Open(str(path),gdal.GA_Update);ds.SetGeoTransform((0,250,0,250000,0,-250));ds=None
    with pytest.raises(ValueError,match='registration'):validate_tiles([(entry,str(path))],product)
    for modified in (xml.replace('./'+stem,'file:///unretained'),xml.replace('<NoDataValue>','<PixelFunctionType>python</PixelFunctionType><NoDataValue>')):
        with pytest.raises(ValueError):vrt_contract(modified.encode(),stem)


def test_store_integrity_idempotency_and_restart(tmp_path):
    store = Store(tmp_path)
    body = {"schema_version": "zeus.acquisition/2.0", "project": "test", "aoi": AOI, "aoi_hash": digest(AOI), "target_crs": "EPSG:4326", "as_of": "2026-01-01T00:00:00Z", "runtime": {}, "policy": "complete-extract/1.0", "selections": [], "findings": [], "estimates": {}}
    h = digest(body)
    store.save_plan(FetchPlan(**body, plan_id=h[:32], plan_hash=h))
    request = {"idempotency_key": "test-key1"}
    job = {"id": "job", "project": "test", "plan_id": h[:32], "status": "pending"}
    store.create_job(job,request)
    assert store.create_job({**job,"id":"another"},request)["id"] == "job"
    store.update("job", {"status": "awaiting_approval"})
    assert Store(tmp_path).job("job")["status"] == "awaiting_approval"
    assert len(store.events("job")) == 2
    with store.connect() as c: c.execute("UPDATE plans SET payload=?", (json.dumps({**body, "project": "changed", "plan_id": h[:32], "plan_hash": h}),))
    with pytest.raises(ValueError,match="integrity"): store.plan(h[:32])


def test_corrupt_blob_rejected(tmp_path):
    store = Store(tmp_path)
    path = tmp_path/"input"; path.write_bytes(b"abc")
    sha, target = store.retain(path)
    target.write_bytes(b"xyz")
    with pytest.raises(ValueError,match="corrupt"): store.verify_blob(sha)


def test_tnm_pagination_and_asset_order():
    from api.dataset_fetch.research.discovery import discover
    class Fake:
        def __init__(self): self.offsets = []
        def json(self,url,params):
            offset=params["offset"]; self.offsets.append(offset)
            rows=[{"sourceId": str(i), "downloadURL": f"https://example.org/{i}.tif"} for i in range(3)]
            return {"total":3, "items":rows[offset:offset+2]}, {"page": offset}
        def inspect(self,a): return a
    fake=Fake()
    assets,_,_=discover(registry()["usgs-3dep-1m"],{},AOI,"2026-01-01T00:00:00Z",fake)
    assert fake.offsets == [0,2]
    assert [a.id for a in assets] == [":0",":1",":2"]


def test_overpass_error_and_geometry_loss():
    from api.dataset_fetch.research.acquire import osm_features
    with pytest.raises(ValueError,match="incomplete"):
        osm_features({"elements":[{"id":1,"type":"way","geometry":[{"lat":1,"lon":2},{}]}]})
    features=osm_features({"elements":[{"id":1,"type":"way","version":3,"tags":{"highway":"path","custom":"retained"},"geometry":[{"lat":1,"lon":2},{"lat":2,"lon":3}]}]})
    assert json.loads(features[0]["properties"]["tags_json"])["custom"] == "retained"


def test_verified_empty_vector(tmp_path):
    from osgeo import ogr,osr
    path=tmp_path/"empty.gpkg"
    ds=ogr.GetDriverByName("GPKG").CreateDataSource(str(path)); ref=osr.SpatialReference(); ref.ImportFromEPSG(4326)
    ds.CreateLayer("roads",ref,ogr.wkbLineString); ds=None
    result,findings=validate_vector(path,AOI,selection_id="roads",expected_count=0)
    assert result["layers"][0]["status"] == "verified_empty"
    assert not findings


def setup_job(tmp_path, monkeypatch, data):
    from api.dataset_fetch.research import worker, planning
    from api.dataset_fetch.research.contracts import AcquisitionReceipt, now
    from api.dataset_fetch.research.worker import new_job
    store = Store(tmp_path/"store")
    project = tmp_path/"project"; project.mkdir()
    ctx = SimpleNamespace(project_path=project, target_epsg=4326)
    monkeypatch.setattr(worker, "context", lambda name: (ctx, AOI))
    src = raster(tmp_path/"source.tif",data)
    sha, blob = store.retain(src)
    receipt = AcquisitionReceipt(asset_id="tile",source_url="https://example.org/tile.tif",sha256=sha,size=blob.stat().st_size,acquired_at=now(),headers={},blob=sha)
    product = registry()["copernicus-glo30"]
    selection = PlannedSelection(id="selection", product=product, parameters={}, assets=[Asset(id="tile",url="https://example.org/tile.tif",filename="tile.tif")], recipe={"target_crs":"EPSG:4326","extent":[0,0,10,10],"spacing":[1,1],"resampling":"near","nodata":-9999,"native_export":False})
    from api.dataset_fetch.research.projection import freeze_raster_operations
    freeze_raster_operations(product,selection.assets,selection.recipe,AOI)
    body={"schema_version":"zeus.acquisition/2.0","project":"test","aoi":AOI,"aoi_hash":digest(AOI),"target_crs":"EPSG:4326","as_of":"2026-01-01T00:00:00Z","runtime":planning.runtime_fingerprint(),"policy":"complete-extract/1.0","selections":[selection.model_dump(mode="json")],"findings":[],"estimates":{"known_download_bytes":1024,"output_upper_bound_bytes":1000,"maximum_asset_bytes":1000000,"maximum_job_bytes":10000000}}
    h=digest(body); plan=FetchPlan(**body,plan_id=h[:32],plan_hash=h); store.save_plan(plan)
    req=ExecuteRequest(plan_id=plan.plan_id,plan_hash=plan.plan_hash,idempotency_key="end-to-end")
    job=new_job(plan,req); store.create_job(job,req.model_dump(mode="json"))
    monkeypatch.setattr(worker,"acquire",lambda *args: [receipt])
    return store,job,project


def test_job_budget_counts_shared_receipts_once_and_rejects_length_conflicts(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    receipt=worker.acquire()[0]
    monkeypatch.setattr(worker,'acquire',lambda *args:[receipt,receipt])
    body={k:v for k,v in store.plan(job['plan_id']).items() if k not in ('plan_id','plan_hash')}
    body['estimates']['maximum_job_bytes']=receipt.size
    identity=digest(body);plan=FetchPlan(**body,plan_id=identity[:32],plan_hash=identity)
    store.save_plan(plan)
    store.update(job['id'],{'plan_id':plan.plan_id,'plan_hash':identity,'approvals':[{'kind':'plan','hash':identity,'finding_ids':[]}]})
    worker.execute(store,job['id'])
    assert store.job(job['id'])['status']=='succeeded'
    transport=Transport(store,retained=[receipt],max_total_bytes=receipt.size)
    transport.account(receipt)
    with pytest.raises(ValueError,match='object length'):
        transport.account(receipt.model_copy(update={'size':receipt.size-1}))


def test_generation_commit_and_offline_replay(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    from api.dataset_fetch.research.replay import replay
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    worker.execute(store,job["id"])
    result=store.job(job["id"])
    assert result["status"] == "succeeded"
    pointer=json.loads((project/"data/active-generation.json").read_text())
    assert pointer["generation"] == job["id"]
    first=replay(job["id"],tmp_path/"replay1",store)
    second=replay(job["id"],tmp_path/"replay2",store)
    assert first["identical_validation"] and second["identical_scientific_content"]


@pytest.mark.parametrize('corrupt',[False,True])
def test_retained_generation_resolves_nested_extent_gap_paths(tmp_path,monkeypatch,corrupt):
    from api.dataset_fetch.research import worker
    from api.dataset_fetch.research.store import atomic_json
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    generation=project/'data/generations/older';folder=generation/'older-selection/grid';folder.mkdir(parents=True)
    old=raster(folder/'old.tif',np.ones((10,10)))
    mask=folder/'extent.gaps.geojson';atomic_json(mask,{'type':'FeatureCollection','features':[]})
    entry={'id':'old','selection_id':'older-selection','path':'data/generations/older/older-selection/grid/old.tif',
           'file':'older-selection/grid/old.tif','sha256':file_hash(old),
           'extent_gap':'older-selection/grid/extent.gaps.geojson','extent_gap_sha256':file_hash(mask)}
    manifest={'schema_version':'zeus.acquisition/2.0','generation':'older','datasets':[entry]}
    atomic_json(generation/'manifest.json',manifest);atomic_json(project/'data/active-generation.json',manifest)
    if corrupt:
        mask.write_bytes(b'corrupt extent mask')
        with pytest.raises(ValueError,match='Previously published artifact'):worker.execute(store,job['id'])
        assert json.loads((project/'data/active-generation.json').read_text())['generation']=='older'
    else:
        worker.execute(store,job['id'])
        assert store.job(job['id'])['status']=='succeeded'
        assert json.loads((project/'data/active-generation.json').read_text())['datasets'][0]==entry


@pytest.mark.parametrize('damage',['manifest','artifact','gap'])
def test_new_generation_cannot_promote_corrupt_previous_content(tmp_path,monkeypatch,damage):
    from api.dataset_fetch.research import worker
    from api.dataset_fetch.research.store import atomic_json
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    older=project/'data/generations/older';older.mkdir(parents=True)
    old=raster(older/'old.tif',np.ones((10,10)))
    gap=raster(older/'gap.tif',np.zeros((10,10)))
    entry={'id':'old','selection_id':'older-selection','path':'data/generations/older/old.tif','sha256':file_hash(old),
           'gap_mask':'gap.tif','gap_path':'data/generations/older/gap.tif','gap_sha256':file_hash(gap)}
    manifest={'schema_version':'zeus.acquisition/2.0','generation':'older','datasets':[entry]}
    atomic_json(older/'manifest.json',manifest)
    if damage=='manifest':manifest['datasets'][0]['name']='unverified change'
    if damage=='artifact':old.write_bytes(b'corrupt older artifact')
    if damage=='gap':gap.write_bytes(b'corrupt older mask')
    atomic_json(project/'data/active-generation.json',manifest)
    with pytest.raises(ValueError,match='immutable generation|Previously published artifact'):
        worker.execute(store,job['id'])
    assert json.loads((project/'data/active-generation.json').read_text())['generation']=='older'
    assert not (project/'data/generations'/job['id']).exists()


@pytest.mark.parametrize('boundary',['before_generation','after_generation','before_pointer','after_pointer','before_ledger_commit','after_ledger_commit'])
def test_real_process_crashes_at_publication_boundaries(tmp_path,monkeypatch,boundary):
    import subprocess
    from api.dataset_fetch.research import worker
    from api.dataset_fetch.research.readers import active_datasets
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    monkeypatch.setattr('api.dataset_fetch.utils.resolve_project_path',lambda name:project)
    worker.execute(store,job['id'])
    old=(project/'data/active-generation.json').read_bytes()
    plan=FetchPlan.model_validate(store.plan(job['plan_id']))
    request=ExecuteRequest(plan_id=plan.plan_id,plan_hash=plan.plan_hash,idempotency_key='second-generation')
    next_job=worker.new_job(plan,request);store.create_job(next_job,request.model_dump(mode='json'))
    publish=worker.publish
    monkeypatch.setattr(worker,'publish',lambda *args:None)
    worker.execute(store,next_job['id'])
    monkeypatch.setattr(worker,'publish',publish)
    assert store.job(next_job['id'])['report']
    script=tmp_path/'publication_child.py'
    script.write_text('''import json,os,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from api.dataset_fetch.research import worker
from api.dataset_fetch.research.store import Store
from api.dataset_fetch import utils
store=Store(Path(sys.argv[2]));root=Path(sys.argv[3]);job_id=sys.argv[4];boundary=sys.argv[5]
plan=store.plan(store.job(job_id)['plan_id'])
worker.context=lambda name:(SimpleNamespace(project_path=root,target_epsg=4326),plan['aoi'])
utils.resolve_project_path=lambda name:root
replace=worker.durable_replace
def move(source,target):
 if boundary=='before_generation':os._exit(73)
 replace(source,target)
 if boundary=='after_generation':os._exit(73)
worker.durable_replace=move
write=worker.atomic_json
def atomic(path,value):
 if path.name=='active-generation.json' and boundary=='before_pointer':os._exit(73)
 write(path,value)
 if path.name=='active-generation.json' and boundary=='after_pointer':os._exit(73)
worker.atomic_json=atomic
event=store._event
def record(connection,identity,kind,payload):
 event(connection,identity,kind,payload)
 if kind=='published' and boundary=='before_ledger_commit':os._exit(73)
store._event=record
worker.publish(store,job_id)
os._exit(73)
''',encoding='utf-8')
    process=subprocess.run([sys.executable,str(script),str(ROOT/'apps/api'),str(store.root),str(project),next_job['id'],boundary],capture_output=True,timeout=30)
    assert process.returncode==73,process.stderr.decode(errors='replace')
    visible=json.loads((project/'data/active-generation.json').read_text())
    before=boundary in ('before_generation','after_generation','before_pointer')
    assert visible['generation']==(job['id'] if before else next_job['id'])
    if before:assert (project/'data/active-generation.json').read_bytes()==old
    assert len(active_datasets(project))==1
    worker.recover(Store(store.root))
    resumed=store.job(next_job['id'])
    if resumed['status']=='publishing':worker.execute(store,next_job['id'])
    assert store.job(next_job['id'])['status']=='succeeded'
    assert all(c['stages']['layer_publish']['status']=='succeeded' for c in store.job(next_job['id'])['categories'].values())
    assert json.loads((project/'data/active-generation.json').read_text())['generation']==next_job['id']
    store.events(next_job['id'])


def test_failure_after_manifest_switch_is_reconciled_not_reported_failed(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    monkeypatch.setattr('api.dataset_fetch.utils.resolve_project_path',lambda name:project)
    atomic=worker.atomic_json
    def failure(path,value):
        atomic(path,value)
        if path.name=='active-generation.json':raise OSError('simulated post-switch error')
    monkeypatch.setattr(worker,'atomic_json',failure)
    with pytest.raises(OSError,match='post-switch'):
        worker.execute(store,job['id'])
    worker.fail_job(store,job['id'],OSError('simulated post-switch error'))
    assert store.job(job['id'])['status']=='succeeded'


def test_aoi_file_container_identity_is_not_scientific_content():
    from api.dataset_fetch.research.contracts import scientific_recipe
    first={'aoi_normalization':{'inputs':[{'sha256':'first'}],'entrypoint':'aoi-original.geojson','normalized_aoi_hash':'geometry','coordinate_operation':{'pipeline':'noop'}}}
    second={'aoi_normalization':{**first['aoi_normalization'],'inputs':[{'sha256':'second'}],'entrypoint':'aoi-renamed.geojson'}}
    assert digest(first)!=digest(second)
    assert digest(scientific_recipe(first))==digest(scientific_recipe(second))


def test_durable_discovery_restart_idempotency_and_no_acquisition(tmp_path,monkeypatch):
    import sqlite3
    from api.dataset_fetch.research import planning,discovery_jobs
    store=Store(tmp_path/'store')
    ctx=SimpleNamespace(target_epsg=4326)
    monkeypatch.setattr(planning,'context',lambda name:(ctx,AOI))
    request=PlanRequest(selections=[{'product_id':'copernicus-glo30'}])
    job=discovery_jobs.submit(store,'fixture',request,'discovery-key')
    assert discovery_jobs.submit(store,'fixture',request,'discovery-key')['id']==job['id']
    with pytest.raises(sqlite3.IntegrityError):discovery_jobs.submit(store,'fixture',request,'second-discovery')
    discovery_jobs.update(store,job['id'],{'status':'running'},'fixture-started')
    discovery_jobs.recover(Store(store.root))
    assert discovery_jobs.get(store,job['id'])['status']=='pending'
    seen=[]
    def build(project,requested,store,*,cancelled,progress):
        seen.append(requested.as_of.isoformat())
        assert not cancelled();progress('discovering','copernicus-glo30')
        return SimpleNamespace(plan_id='frozen',plan_hash='hash')
    monkeypatch.setattr(planning,'build_plan',build)
    discovery_jobs.execute(store,job['id'])
    result=discovery_jobs.get(store,job['id'])
    assert result['status']=='succeeded' and result['plan_id']=='frozen'
    assert seen and not store.jobs()
    assert store.events(job['id'])[-1]['kind']=='discovery_finished'


def test_discovery_cancellation_retains_lock_until_worker_stops(tmp_path,monkeypatch):
    import sqlite3
    from api.dataset_fetch.research import planning,discovery_jobs
    from api.dataset_fetch.research.transport import Cancelled
    store=Store(tmp_path/'store')
    monkeypatch.setattr(planning,'context',lambda name:(SimpleNamespace(target_epsg=4326),AOI))
    request=PlanRequest(selections=[{'product_id':'copernicus-glo30'}])
    job=discovery_jobs.submit(store,'fixture',request,'discovery-key')
    def build(project,requested,store,*,cancelled,progress):
        discovery_jobs.update(store,job['id'],{'status':'cancelling'},'cancel_requested')
        with pytest.raises(sqlite3.IntegrityError):discovery_jobs.submit(store,'fixture',request,'second-key')
        assert cancelled()
        raise Cancelled('provider process stopped')
    monkeypatch.setattr(planning,'build_plan',build)
    discovery_jobs.execute(store,job['id'])
    assert discovery_jobs.get(store,job['id'])['status']=='cancelled'
    assert discovery_jobs.submit(store,'fixture',request,'second-key')['status']=='pending'
    assert not store.jobs()


@pytest.mark.parametrize('race',['stage','provider_failure'])
def test_discovery_cancellation_wins_a_stage_or_provider_failure_race(tmp_path,monkeypatch,race):
    from api.dataset_fetch.research import planning,discovery_jobs
    store=Store(tmp_path/'store')
    monkeypatch.setattr(planning,'context',lambda name:(SimpleNamespace(target_epsg=4326),AOI))
    job=discovery_jobs.submit(store,'fixture',PlanRequest(selections=[{'product_id':'copernicus-glo30'}]),'discovery-race')
    def build(project,requested,store,*,cancelled,progress):
        discovery_jobs.update(store,job['id'],{'status':'cancelling'},'cancel_requested')
        if race=='stage':discovery_jobs.update(store,job['id'],{'stage':'next'},'discovery_stage',('running',))
        raise OSError('provider failed while cancellation was pending')
    monkeypatch.setattr(planning,'build_plan',build)
    discovery_jobs.execute(store,job['id'])
    result=discovery_jobs.get(store,job['id'])
    assert result['status']=='cancelled' and result['plan_id'] is None
    assert not store.jobs() and store.events(job['id'])[-1]['kind']=='discovery_cancelled'


def test_changed_aoi_prevents_queued_discovery(tmp_path,monkeypatch):
    from api.dataset_fetch.research import planning,discovery_jobs
    store=Store(tmp_path/'store');ctx=SimpleNamespace(target_epsg=4326)
    monkeypatch.setattr(planning,'context',lambda name:(ctx,AOI))
    job=discovery_jobs.submit(store,'fixture',PlanRequest(selections=[{'product_id':'copernicus-glo30'}]),'discovery-key')
    ctx.target_epsg=3857
    discovery_jobs.execute(store,job['id'])
    result=discovery_jobs.get(store,job['id'])
    assert result['status']=='failed' and 'changed' in result['error'] and result['plan_id'] is None


def test_gap_held_until_exact_report_approval(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    data=np.ones((10,10)); data[4:6,4:6]=-9999
    store,job,project=setup_job(tmp_path,monkeypatch,data)
    worker.execute(store,job["id"])
    value=store.job(job["id"])
    assert value["status"] == "awaiting_approval"
    assert not (project/"data/active-generation.json").exists()
    report=value["report"]
    value["approvals"].append({"kind":"report","hash":report["report_hash"],"finding_ids":[f["id"] for f in report["findings"] if f["severity"] == "acknowledgement"]})
    store.update(job["id"],{"status":"publishing","approvals":value["approvals"]})
    worker.publish(store,job["id"])
    assert store.job(job["id"])["status"] == "succeeded"


def test_cancellation_before_commit_leaves_old_generation(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    from api.dataset_fetch.research.transport import Cancelled
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    original=worker.publish
    def cancel_then_publish(store,id):
        store.update(id,{"status":"cancelling"})
        return original(store,id)
    monkeypatch.setattr(worker,"publish",cancel_then_publish)
    with pytest.raises(Cancelled): worker.execute(store,job["id"])
    assert not (project/"data/active-generation.json").exists()


def test_portable_bundle_replays_without_project_or_original_store(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    from api.dataset_fetch.research.bundle import write_bundle, extract_bundle, import_directory
    from api.dataset_fetch.research.replay import replay
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    worker.execute(store,job["id"])
    job=store.job(job["id"])
    plan=store.plan(job["plan_id"])
    bundle=tmp_path/'bundle.zip'
    write_bundle(bundle,plan,job,{'validation':job['report'],'events':store.events(job['id'])},store,project/'data/generations'/job['id'])
    destination=tmp_path/'portable'
    extract_bundle(bundle,destination)
    other=Store(tmp_path/'fresh-store')
    copied=import_directory(destination,other)
    assert replay(copied,tmp_path/'portable-replay',other)['identical_validation']
    # Exercise the extracted implementation in a fresh interpreter and directory.
    # Importing the checkout's replay module alone cannot prove portability.
    import subprocess
    completed = subprocess.run([sys.executable, str(destination/'zeus_replay.py'), '--output', str(tmp_path/'standalone-replay')],
                               cwd=destination, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads((tmp_path/'standalone-replay/replay-result.json').read_text())['identical_validation']
    first=next((destination/'blobs').rglob('*'*0+'[0-9a-f]'*64))
    first.write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='corrupt'):
        import_directory(destination,Store(tmp_path/'bad-store'))


def test_wrong_units_cannot_be_approved(tmp_path):
    from osgeo import gdal
    path=raster(tmp_path/'wrong-units.tif',np.ones((10,10)))
    ds=gdal.Open(str(path),gdal.GA_Update); ds.GetRasterBand(1).SetUnitType('ft'); ds=None
    with pytest.raises(ValueError,match='units'):
        validate_raster(path,AOI,registry()['copernicus-glo30'],selection_id='x')


def test_sentinel_calibration_and_required_offsets():
    from api.dataset_fetch.research.imagery import calibration
    xml=b'<root><PROCESSING_BASELINE>04.00</PROCESSING_BASELINE><BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE><BOA_ADD_OFFSET band_id="3">-1000</BOA_ADD_OFFSET></root>'
    value=calibration(xml,'04.00',['B04'])['B04']
    assert 2000*value['scale']+value['offset'] == pytest.approx(.1)
    with pytest.raises(ValueError,match='offset'): calibration(xml,'04.00',['B02'])
    with pytest.raises(ValueError,match='baseline'): calibration(xml,'05.00',['B04'])


def test_antimeridian_normalization_preserves_holes():
    from api.dataset_fetch.research.spatial import normalize_aoi
    from api.dataset_fetch.research.planning import split_bounds
    from shapely.geometry import shape
    value={'type':'Polygon','coordinates':[[[179,10],[-179,10],[-179,12],[179,12],[179,10]],[[179.2,10.2],[179.2,10.8],[179.8,10.8],[179.8,10.2],[179.2,10.2]]]}
    normalized=normalize_aoi(value)
    assert len(list(split_bounds(normalized))) == 2
    assert shape(normalized).area == pytest.approx(4-.36)


def test_project_aoi_antimeridian_hole_preserves_original_file(tmp_path):
    from api.dataset_fetch.research.aoi import read_aoi
    from shapely.geometry import shape
    value={'type':'Polygon','coordinates':[[[179,10],[-179,10],[-179,12],[179,12],[179,10]],[[179.2,10.2],[179.2,10.8],[179.8,10.8],[179.8,10.2],[179.2,10.2]]]}
    path=tmp_path/'crossing.geojson'
    raw=canonical({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':value}]});path.write_bytes(raw)
    aoi,evidence=read_aoi(path)
    assert aoi['type']=='MultiPolygon' and shape(aoi).area==pytest.approx(3.64)
    assert path.read_bytes()==raw
    replayed,_=read_aoi(path,frozen=evidence)
    assert replayed==aoi


def test_gap_outside_raster_extent_has_downloadable_geometry(tmp_path):
    aoi={'type':'Polygon','coordinates':[[[-1,0],[10,0],[10,10],[-1,10],[-1,0]]]}
    validate_raster(raster(tmp_path/'data.tif',np.ones((10,10))),aoi,registry()['copernicus-glo30'],selection_id='x',gap_path=tmp_path/'gaps.tif')
    from shapely.geometry import shape
    gaps=json.loads((tmp_path/'gaps.geojson').read_text())
    assert shape(gaps['features'][0]['geometry']).area == pytest.approx(10)


def climate_fixture(path,variable='t2m',units='K',step='instant',calendar='proleptic_gregorian',hours=None):
    import xarray as xr
    times=np.arange(24) if hours is None else hours
    data=xr.Dataset({variable:(('time','latitude','longitude'),np.ones((len(times),2,2)),{'units':units,'GRIB_stepType':step})},coords={
        'time':('time',times,{'units':'hours since 2020-01-01 00:00:00','calendar':calendar}),
        'latitude':('latitude',[45.,44.],{'units':'degrees_north'}),'longitude':('longitude',[-80.,-79.],{'units':'degrees_east'})})
    data.to_netcdf(path,engine='netcdf4')
    return path


def test_climate_all_times_and_accumulation_semantics(tmp_path):
    from api.dataset_fetch.research.climate import validate_climate
    path=climate_fixture(tmp_path/'ok.nc')
    assert validate_climate(path,['2m_temperature'],'2020-01-01')['variables'][0]['values_scanned'] == 96
    for label,kwargs,match in [('missing',{'hours':np.arange(23)},'time steps'),('half-hour',{'hours':np.arange(24)+.5},'time steps'),('calendar',{'calendar':'360_day'},'calendar')]:
        with pytest.raises(ValueError,match=match):
            validate_climate(climate_fixture(tmp_path/f'{label}.nc',**kwargs),['2m_temperature'],'2020-01-01')
    with pytest.raises(ValueError,match='accumulation'):
        validate_climate(climate_fixture(tmp_path/'accum.nc',variable='tp',units='m'),['total_precipitation'],'2020-01-01')


def test_climate_explicit_time_and_temperature_semantics(tmp_path):
    from api.dataset_fetch.research.climate import validate_climate
    from api.dataset_fetch.research.netcdf_compat import load_netcdf
    path=climate_fixture(tmp_path/'source.nc')
    initial=validate_climate(path,['2m_temperature'],'2020-01-01')
    assert initial['time_units_metadata']=='leap_seconds: unknown'
    with load_netcdf().Dataset(path,'a') as ds:ds['time'].units_metadata='leap_seconds: none'
    explicit=validate_climate(path,['2m_temperature'],'2020-01-01')
    assert explicit['scientific_hash']!=initial['scientific_hash']
    with load_netcdf().Dataset(path,'a') as ds:ds['time'].units_metadata='leap_seconds: utc'
    with pytest.raises(ValueError,match='leap-second'):validate_climate(path,['2m_temperature'],'2020-01-01')
    with load_netcdf().Dataset(path,'a') as ds:
        ds['time'].units_metadata='leap_seconds: none'
        ds['t2m'].units_metadata='temperature: difference'
    with pytest.raises(ValueError,match='absolute temperature'):validate_climate(path,['2m_temperature'],'2020-01-01')


def test_corrupt_map_cache_is_never_reused(tmp_path):
    from api.data import _write_cache_file,_read_cache_file
    path=tmp_path/'tile.png'
    _write_cache_file(path,b'good')
    assert _read_cache_file(path)==b'good'
    path.write_bytes(b'evil')
    assert _read_cache_file(path) is None


def test_preview_masks_holes_and_gaps_without_modifying_native_values(tmp_path):
    import io,math
    from PIL import Image
    from api.data import render_raster_tile
    source=raster(tmp_path/'native.tif',np.full((10,10),12.5))
    gaps=np.zeros((10,10));gaps[0,9]=1
    gap=raster(tmp_path/'gaps.tif',gaps,nodata=255)
    aoi={**AOI,'coordinates':AOI['coordinates']+[[[4,4],[4,6],[6,6],[6,4],[4,4]]]}
    before=file_hash(source)
    png=render_raster_tile(source,5,16,15,json.dumps(aoi),str(gap),False)
    alpha=np.asarray(Image.open(io.BytesIO(png)).convert('RGBA'))[:,:,3]
    def opacity(lon,lat):
        x=int(lon/11.25*256)
        mercator=math.log(math.tan(math.pi/4+math.radians(lat)/2))*6378137
        span=2*math.pi*6378137/32
        return alpha[int((span-mercator)/span*256),x]
    assert opacity(2,2)==255 and opacity(5,5)==0 and opacity(10.5,5)==0 and opacity(9.5,9.5)==0
    assert file_hash(source)==before


def test_preview_keeps_subcell_aoi_when_no_native_cell_center_is_inside(tmp_path):
    import io,math
    from PIL import Image
    from api.data import render_raster_tile
    source=raster(tmp_path/'coarse.tif',np.full((10,10),7001))
    gap=raster(tmp_path/'gaps.tif',np.zeros((10,10)),nodata=255)
    # This entire AOI is inside one native cell but excludes its center (2.5,2.5).
    aoi={'type':'Polygon','coordinates':[[[2.05,2.05],[2.35,2.05],[2.35,2.35],[2.05,2.35],[2.05,2.05]]]}
    before=file_hash(source)
    image=np.asarray(Image.open(io.BytesIO(render_raster_tile(source,5,16,15,json.dumps(aoi),str(gap),True))).convert('RGBA'))
    def opacity(lon,lat):
        span=2*math.pi*6378137/32
        northing=math.log(math.tan(math.pi/4+math.radians(lat)/2))*6378137
        return image[int((span-northing)/span*256),int(lon/11.25*256),3]
    assert opacity(2.2,2.2)==255 and opacity(2.5,2.5)==0
    assert file_hash(source)==before


def test_portable_preview_reads_verified_generation_plan_without_local_ledger(tmp_path,monkeypatch):
    from api.dataset_fetch.research import preview
    from api.dataset_fetch.research.store import atomic_json
    generation=tmp_path/'data/generations/old';generation.mkdir(parents=True)
    source=raster(generation/'native.tif',np.ones((10,10)))
    body={'aoi':AOI,'aoi_hash':digest(AOI)};identity=digest(body)
    plan={**body,'plan_hash':identity,'plan_id':identity[:32]}
    atomic_json(generation/'plan.json',plan)
    atomic_json(generation/'manifest.json',{'plan_hash':identity})
    item={'path':source.relative_to(tmp_path).as_posix(),'sha256':file_hash(source),'category':'soil'}
    monkeypatch.setattr(preview,'active_datasets',lambda path:[item])
    monkeypatch.setattr(Store,'plan',lambda *args:pytest.fail('Portable preview must not need the local ledger'))
    assert json.loads(preview.preview_context(tmp_path,source)['aoi_json'])==AOI
    plan['aoi']={'type':'Polygon','coordinates':[]}
    atomic_json(generation/'plan.json',plan)
    with pytest.raises(ValueError,match='plan identity'):preview.preview_context(tmp_path,source)


def test_preview_cache_binds_aoi_recipe_and_validity_integrity(tmp_path,monkeypatch):
    from api.dataset_fetch.research import preview
    source=raster(tmp_path/'native.tif',np.ones((10,10)))
    gap=raster(tmp_path/'gaps.tif',np.zeros((10,10)),nodata=255)
    item={'path':source.name,'sha256':file_hash(source),'aoi':AOI,'aoi_hash':digest(AOI),'gap_path':gap.name,'gap_sha256':file_hash(gap),'category':'population','recipe':{'native_export':True}}
    monkeypatch.setattr(preview,'active_datasets',lambda path:[item])
    first=preview.preview_context(tmp_path,source)['identity']
    item['aoi']={**AOI,'coordinates':AOI['coordinates']+[[[4,4],[4,6],[6,6],[6,4],[4,4]]]};item['aoi_hash']=digest(item['aoi'])
    second=preview.preview_context(tmp_path,source)['identity']
    assert second!=first
    item['recipe']['preview_month']=1
    assert preview.preview_context(tmp_path,source)['identity']!=second
    gap.write_bytes(b'corrupted validity')
    with pytest.raises(ValueError,match='validity mask'):preview.preview_context(tmp_path,source)


@pytest.mark.parametrize('quantity,units',[('count','people/cell'),('density','people/km2')])
def test_population_native_values_are_preserved(tmp_path,quantity,units):
    from api.dataset_fetch.research.processing import run_selection
    from api.dataset_fetch.research.contracts import AcquisitionReceipt,now
    from osgeo import gdal
    values=np.arange(100,dtype=np.float32).reshape(10,10)/3
    path=raster(tmp_path/'population.tif',values)
    store=Store(tmp_path/'store'); sha,blob=store.retain(path)
    product=registry()['worldpop-counts'].model_copy(deep=True)
    product.units=units; product.semantics['quantity']=quantity
    selection=PlannedSelection(id='pop',product=product,parameters={'year':2020},assets=[Asset(id='pop',url='https://example.org/pop.tif',filename='pop.tif')],recipe={'native_export':True})
    receipt=AcquisitionReceipt(asset_id='pop',source_url='https://example.org/pop.tif',sha256=sha,size=blob.stat().st_size,acquired_at=now(),headers={},blob=sha)
    outputs,results,findings=run_selection(selection,[receipt],SimpleNamespace(aoi=AOI),store,tmp_path/'output')
    actual=gdal.Open(str(tmp_path/'output'/outputs[0]['file'])).ReadAsArray()
    assert np.array_equal(actual,values)
    assert results[0]['bands'][0]['valid_fraction']==1
    assert not findings


def test_failed_ledger_write_never_publishes(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    import sqlite3
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    original=store.update
    def fail_report(id,changes,**kwargs):
        if kwargs.get('event')=='validated': raise sqlite3.OperationalError('disk full')
        return original(id,changes,**kwargs)
    monkeypatch.setattr(store,'update',fail_report)
    with pytest.raises(sqlite3.OperationalError): worker.execute(store,job['id'])
    assert not (project/'data/active-generation.json').exists()


def test_corrupted_output_blocks_atomic_publication(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    original=worker.publish
    def corrupt_then_publish(store,id):
        current=store.job(id); (Path(current['staging'])/current['outputs'][0]['file']).write_bytes(b'corrupted')
        return original(store,id)
    monkeypatch.setattr(worker,'publish',corrupt_then_publish)
    with pytest.raises(ValueError,match='changed'): worker.execute(store,job['id'])
    assert not (project/'data/active-generation.json').exists()


def test_retry_rate_limit_and_credential_expiry(tmp_path,monkeypatch):
    from api.dataset_fetch.research import transport
    class Response:
        def __init__(self,status): self.status_code=status; self.ok=status<400; self.headers={'Retry-After':'1'}
        def close(self): pass
    client=Transport(Store(tmp_path/'store')); responses=iter([Response(429),Response(503),Response(200)])
    monkeypatch.setattr(client,'isolated_request',lambda *args,**kwargs:next(responses))
    monkeypatch.setattr(transport.time,'sleep',lambda seconds:None)
    assert client.request('GET','https://example.org/data').status_code==200
    monkeypatch.setattr(client,'isolated_request',lambda *args,**kwargs:Response(401))
    with pytest.raises(ValueError,match='401'): client.request('GET','https://example.org/data')


def test_receipt_url_preserves_bare_query_flags_without_credentials():
    from api.dataset_fetch.research.transport import safe_url
    url='https://example.org/download?area=GB&format=ASCII+Grid&redirect&empty=&key=secret&%73ig=secret&part=1&part=2#fragment'
    assert safe_url(url)=='https://example.org/download?area=GB&format=ASCII+Grid&redirect&empty=&part=1&part=2'


def test_documentation_retained_and_remote_failure_distinct_from_local_failure(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from api.dataset_fetch.research import evidence
    from api.dataset_fetch.research.transport import ProviderResponseError, Cancelled
    monkeypatch.setattr(evidence,'ROOT',tmp_path)
    client=Transport(Store(tmp_path/'store'))
    item=registry()['worldcover-2021']
    item.assessment.documentation=['https://example.org/provider-documentation']
    @contextmanager
    def response(*args,**kwargs):
        yield SimpleNamespace(headers={'Content-Type':'text/plain'},payload=b'Published scientific method')
    monkeypatch.setattr(client,'request',response)
    monkeypatch.setattr(client,'chunks',lambda response:[response.payload])
    snapshots,findings=evidence.documentary_evidence(item,client)
    assert not findings and len(snapshots)==1
    assert snapshots[0]['documentation']['retrieved_at']
    assert client.store.verify_blob(snapshots[0]['receipt']['sha256']).read_bytes()==b'Published scientific method'
    def fail(error):
        def request(*args,**kwargs):raise error
        monkeypatch.setattr(client,'request',request)
    fail(ProviderResponseError('Provider HTTP 404'))
    snapshots,findings=evidence.documentary_evidence(item,client)
    assert not snapshots and findings[0].severity=='acknowledgement'
    for error in (OSError('disk full'),ValueError('Job input budget exhausted'),Cancelled('cancelled')):
        fail(error)
        with pytest.raises(type(error),match=str(error)):
            evidence.documentary_evidence(item,client)


def test_http_child_persistence_failure_remains_fatal(tmp_path,monkeypatch):
    import subprocess
    from api.dataset_fetch.research import transport
    real=subprocess.Popen
    def failure(*args,**kwargs):
        return real([sys.executable,'-c',"import sys,json;sys.stdin.read();print(json.dumps({'error':'OSError','retryable':False,'failure_kind':'persistence'}))"],**kwargs)
    monkeypatch.setattr(transport.subprocess,'Popen',failure)
    client=Transport(Store(tmp_path/'store'))
    with pytest.raises(OSError,match='durably retain'):
        client.request('GET','https://example.org/evidence')
    assert not list(client.store.root.glob('http-*'))


def test_source_change_and_disk_exhaustion_stop_retention(tmp_path,monkeypatch):
    import io
    class Raw:
        def __init__(self): self.stream=io.BytesIO(b'data')
        def read1(self,n,decode_content=True): return self.stream.read(n)
    class Response:
        headers={'ETag':'new','Content-Length':'4'}
        def __init__(self): self.raw=Raw()
        def __enter__(self): return self
        def __exit__(self,*args): pass
    client=Transport(Store(tmp_path/'store'))
    monkeypatch.setattr(client,'request',lambda *args,**kwargs:Response())
    with pytest.raises(ValueError,match='ETag changed'): client.download(Asset(id='a',url='https://example.org/a',filename='a',etag='old'))
    import shutil
    monkeypatch.setattr(shutil,'disk_usage',lambda path:SimpleNamespace(free=0))
    with pytest.raises(ValueError,match='disk space'): client.download(Asset(id='a',url='https://example.org/a',filename='a'))
    assert not list((client.store.root/'blobs').rglob('*')) if (client.store.root/'blobs').exists() else True


@pytest.mark.parametrize('cancel',[False,True])
def test_stalled_http_subprocess_stops_before_job_can_continue(tmp_path,monkeypatch,cancel):
    import subprocess,time
    from api.dataset_fetch.research import transport
    real = subprocess.Popen
    children=[]
    def hang(*args,**kwargs):
        child=real([sys.executable,'-c','import sys,time; sys.stdin.read(); time.sleep(60)'],**kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(transport.subprocess,'Popen',hang)
    started=time.monotonic()
    client=Transport(Store(tmp_path/'store'),cancelled=lambda: cancel and time.monotonic()-started>.2)
    with pytest.raises(transport.Cancelled if cancel else ValueError):
        client.isolated_request('GET','https://example.org/stalled',deadline=.5)
    assert children[0].poll() is not None
    assert not list(client.store.root.glob('http-*'))


def test_worldclim_cf_preserves_every_month_and_native_value(tmp_path):
    from api.dataset_fetch.research.climatology import export_climatology
    from api.dataset_fetch.research.netcdf_compat import load_netcdf
    data=np.stack([np.full((10,10),i+.25) for i in range(12)])
    source=raster(tmp_path/'monthly.tif',data)
    mask=raster(tmp_path/'mask.tif',np.zeros_like(data),nodata=255)
    result=export_climatology(source,mask,tmp_path/'monthly.nc',registry()['worldclim-tavg'],'fixture-hash')
    with load_netcdf().Dataset(tmp_path/'monthly.nc') as ds:
        assert np.array_equal(ds['tavg'][:],data)
        assert ds['time'].climatology=='climatology_bounds'
        assert ds['time'].calendar=='proleptic_gregorian'
        assert ds['time'].units_metadata=='leap_seconds: none'
        assert ds['tavg'].units=='degree_Celsius'
        assert ds['tavg'].units_metadata=='temperature: on_scale'
        assert 'without resampling' in ds.history
    assert result['months']==12 and result['native_values_preserved']


def test_arcgis_partition_reconciles_ids_and_service_batch_limit():
    from api.dataset_fetch.research.discovery import discover
    product=registry()['can-clss']
    class Client:
        def json(self,url,params):
            if not url.endswith('/query'):
                return {'fields':[{'name':'OBJECTID','type':'esriFieldTypeOID'}],'extent':{'spatialReference':{'wkid':4326}},'maxRecordCount':1},{}
            bounds=list(map(float,params['geometry'].split(',')))
            ids=[1,2] if bounds[2]-bounds[0]==10 else [1] if bounds[0]==0 else [2]
            if params.get('returnCountOnly'):
                return {'count':len(ids)},{}
            return {'objectIds':ids[:1],'exceededTransferLimit':len(ids)>1},{}
    assets,_,_=discover(product,{},AOI,'2026-01-01T00:00:00Z',Client())
    assert [a.metadata['ids'] for a in assets]==[[1],[2]]


def test_zenodo_uses_publisher_filename_not_api_content_path():
    from api.dataset_fetch.research.discovery import discover
    product=registry()['gem-pga-2023']
    class Client:
        def json(self,url):
            return {'files':[{'key':product.semantics['filename'],'links':{'self':'https://zenodo.org/api/records/8409647/files/archive/content'},'checksum':'md5:'+'0'*32,'size':123}]},{}
    assets,_,_=discover(product,{},AOI,'2026-01-01T00:00:00Z',Client())
    assert assets[0].filename.endswith('.zip')
    assert assets[0].expected_hash == '0'*32


@pytest.mark.parametrize('mode',['empty','complete','truncated','missing-id','changed'])
def test_ogc_inventory_reconciliation(tmp_path,mode):
    from api.dataset_fetch.research.acquire import acquire
    product=registry()['can-clss'].model_copy(update={'adapter':'ogc_features','endpoint':'https://example.org/items'})
    selection=PlannedSelection(id='ogc',product=product,parameters={},assets=[],recipe={},discovery=[{'ogc_features':{'boxes':[[0,0,10,10]]}}])
    class Client(Transport):
        def json(self,url,params=None):
            second = 'page=2' in url
            ids=[] if mode=='empty' else [2 if second else 1]
            payload={'type':'FeatureCollection','numberMatched':0 if mode=='empty' else 3 if mode=='changed' and second else 2,
                     'numberReturned':len(ids),'features':[{'type':'Feature','id':None if mode=='missing-id' else i,'properties':{},'geometry':None} for i in ids],
                     'links':[{'rel':'next','href':'?page=2'}] if not second and mode not in ('empty','truncated') else []}
            return payload,{'receipt':self.retain_bytes(canonical(payload),url).model_dump(mode='json')}
    client=Client(Store(tmp_path/'store'))
    checkpoints={}
    if mode in ('empty','complete'):
        receipts=acquire(selection,client,{},lambda values:checkpoints.update(values))
        assert len(receipts)==(1 if mode=='empty' else 2)
        assert checkpoints
    else:
        with pytest.raises(ValueError): acquire(selection,client,{},lambda values:checkpoints.update(values))
        assert not checkpoints


def test_parent_death_terminates_http_child(tmp_path):
    import subprocess,time
    if os.name != 'nt': pytest.skip('Windows process handle qualification; POSIX requires its own platform qualification')
    parent=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    code="import sys,time; sys.path.insert(0,sys.argv[1]); from api.dataset_fetch.research.http_transfer import watch_parent; watch_parent(int(sys.argv[2])); print('ready',flush=True); time.sleep(60)"
    child=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'apps/api'),str(parent.pid)],stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ready'
        parent.terminate(); parent.wait(timeout=5)
        assert child.wait(timeout=5)==72
    finally:
        for process in (child,parent):
            if process.poll() is None: process.kill(); process.wait(timeout=5)
        child.stdout.close()


def test_total_input_budget_stops_before_next_retention(tmp_path):
    client=Transport(Store(tmp_path/'store'),max_total_bytes=5)
    client.retain_bytes(b'abcd','https://example.org/first')
    assert client.remaining_bytes==1
    with pytest.raises(ValueError,match='remaining'): client.retain_bytes(b'ef','https://example.org/second')
    assert sum(client.retained_sizes.values())==4


@pytest.mark.parametrize('members',[
    {'../escape.tif':b'data'},
    {'data.shp':b'data'},
    {'data.tif':b'<VRTDataset><SourceFilename>/vsicurl/https://example.org/live.tif</SourceFilename></VRTDataset>'},
    {'data.tif':b'II*\0' + b'0'*20,'DATA.tif':b'II*\0'+b'0'*20},
])
def test_archive_dependencies_and_virtual_inputs_fail_closed(tmp_path,members):
    import zipfile
    from api.dataset_fetch.research.national import inspect_archive
    path=tmp_path/'bad.zip'
    with zipfile.ZipFile(path,'w') as archive:
        for name,value in members.items(): archive.writestr(name,value)
    with pytest.raises(ValueError): inspect_archive(path)


def test_catalogue_aliases_do_not_substitute_products():
    import importlib.util
    spec=importlib.util.spec_from_file_location('catalogue_audit',ROOT/'tests/audit_catalogue.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    def match(name,kind='Satellite Imagery',country='WLD'):
        row={'Dataset':name,'Type':kind,'ISO3':country,'URL':''}
        return module.match(row,module.category(row),registry())
    assert match('Esri Sentinel-2 Land Use/Land Cover','Land Cover / Land Use')==[]
    assert match('Copernicus DEM GLO-90','Elevation')==['copernicus-glo90']
    assert match('ESA WorldCover 2021','Land Cover')==['worldcover-2021']
    assert match('WorldPop Qatar Population Density','Population')==[]
    assert match('World Database on Protected Areas','Protected Areas','CAN')==[]


def test_climate_cf_bounds_and_per_hour_gap_masks(tmp_path):
    import xarray as xr
    from api.dataset_fetch.research.climate_spatial import export_climate
    from api.dataset_fetch.research.netcdf_compat import load_netcdf
    data=np.ones((24,2,2)); data[3,0,0]=np.nan
    path=tmp_path/'source.nc'
    dataset=xr.Dataset({'tp':(('time','latitude','longitude'),data,{'units':'m','GRIB_stepType':'accum'})},coords={
        'time':('time',np.arange(24),{'units':'hours since 2020-01-01 00:00:00','calendar':'proleptic_gregorian'}),
        'latitude':('latitude',[.375,.125],{'units':'degrees_north'}),'longitude':('longitude',[.125,.375],{'units':'degrees_east'})})
    dataset.to_netcdf(path,engine='netcdf4')
    aoi={'type':'Polygon','coordinates':[[[0,0],[.5,0],[.5,.5],[0,.5],[0,0]]]}
    selection=PlannedSelection(id='climate',product=registry()['era5-single-levels'],parameters={'variables':['total_precipitation']},assets=[],recipe={})
    results,findings,outputs=export_climate(path,tmp_path/'export.nc',selection,SimpleNamespace(aoi=aoi),'2020-01-01')
    assert len(findings)==1 and findings[0].severity=='acknowledgement'
    assert results[0]['bands'][3]['valid_fraction']==pytest.approx(.75)
    assert all(b['valid_fraction']==1 for i,b in enumerate(results[0]['bands']) if i!=3)
    with load_netcdf().Dataset(tmp_path/'export.nc') as value:
        assert np.array_equal(value['zeus_time_bounds'][0],[-1,0])
        assert value['tp'].cell_methods=='time: sum'
        assert value['time'].units_metadata=='leap_seconds: unknown'
        assert '_FillValue' not in value['latitude'].ncattrs()
        assert 'units' not in value['zeus_time_bounds'].ncattrs()
        assert 'without resampling' in value.history
        assert value['zeus_tp_aoi_validity'][3,0,0]==1
    with xr.open_dataset(tmp_path/'export.nc') as value:
        assert np.array_equal(value.tp.values,data,equal_nan=True)


def test_climate_wrong_native_registration_blocks_export(tmp_path):
    from api.dataset_fetch.research.climate_spatial import export_climate
    selection=PlannedSelection(id='climate',product=registry()['era5-single-levels'],parameters={'variables':['2m_temperature']},assets=[],recipe={})
    with pytest.raises(ValueError,match='spacing'):
        export_climate(climate_fixture(tmp_path/'source.nc'),tmp_path/'export.nc',selection,SimpleNamespace(aoi=AOI),'2020-01-01')


def test_osm_relation_members_are_retained_or_blocked():
    from api.dataset_fetch.research.acquire import osm_features
    relation={'type':'relation','id':10,'version':3,'tags':{'type':'route','route':'road'},'members':[
        {'type':'way','ref':11,'role':'forward','geometry':[{'lon':1,'lat':1},{'lon':2,'lat':2}]},
        {'type':'node','ref':12,'role':'stop','lat':1.5,'lon':1.5}]}
    features=osm_features({'elements':[relation]})
    assert [f['geometry']['type'] for f in features]==['LineString','Point']
    assert features[1]['properties']['member_index']==1 and features[1]['properties']['member_ref']=='12'
    relation['members'].append({'type':'relation','ref':13})
    with pytest.raises(ValueError,match='nested'): osm_features({'elements':[relation]})


def test_polar_aoi_complete_native_scan(tmp_path):
    from osgeo import gdal,osr
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    import math
    aoi={'type':'Polygon','coordinates':[[[-40,85],[-39.9,85],[-39.9,85.05],[-40,85.05],[-40,85]]]}
    bounds=transform(Transformer.from_crs(4326,3413,always_xy=True).transform,shape(aoi)).bounds
    x0,y0=math.floor(bounds[0]/100)*100,math.ceil(bounds[3]/100)*100
    w,h=math.ceil((bounds[2]-x0)/100),math.ceil((y0-bounds[1])/100)
    path=tmp_path/'polar.tif'
    dataset=gdal.GetDriverByName('GTiff').Create(str(path),w,h,1,gdal.GDT_Float32)
    crs=osr.SpatialReference(); crs.ImportFromEPSG(3413)
    dataset.SetProjection(crs.ExportToWkt()); dataset.SetGeoTransform((x0,100,0,y0,0,-100))
    dataset.GetRasterBand(1).WriteArray(np.ones((h,w),dtype=np.float32)); dataset=None
    result,findings=validate_raster(path,aoi,registry()['copernicus-glo30'],selection_id='polar')
    assert result['bands'][0]['valid_fraction']==pytest.approx(1)
    assert not findings


def test_restart_rechecks_checkpoint_integrity(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    from api.dataset_fetch import utils
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    receipt=worker.acquire()[0]
    store.update(job['id'],{'status':'running','checkpoints':{'selection:tile':receipt.model_dump(mode='json')}})
    monkeypatch.setattr(utils,'resolve_project_path',lambda name:project)
    restarted=Store(store.root)
    worker.recover(restarted)
    assert restarted.job(job['id'])['status']=='pending'
    restarted.blob_path(receipt.sha256).write_bytes(b'corrupt after checkpoint')
    with pytest.raises(ValueError,match='corrupt'): worker.execute(restarted,job['id'])
    assert not (project/'data/active-generation.json').exists()


def test_publication_rejects_unexecuted_mandatory_stage(tmp_path,monkeypatch):
    from api.dataset_fetch.research import worker
    store,job,project=setup_job(tmp_path,monkeypatch,np.ones((10,10)))
    original=worker.publish
    def skip(store,id):
        states=store.job(id)['categories']
        states['selection']['stages']['raw_metadata']['status']='queued'
        store.update(id,{'categories':states})
        original(store,id)
    monkeypatch.setattr(worker,'publish',skip)
    with pytest.raises(ValueError,match='mandatory'): worker.execute(store,job['id'])
    assert not (project/'data/active-generation.json').exists()


def test_copernicus_polar_longitude_spacing_is_explicit():
    from api.dataset_fetch.research.discovery import discover
    aoi={'type':'Polygon','coordinates':[[[10,86],[10.1,86],[10.1,86.1],[10,86.1],[10,86]]]}
    client=SimpleNamespace(inspect=lambda asset:asset)
    assets,_,_=discover(registry()['copernicus-glo30'],{},aoi,'2026-01-01T00:00:00Z',client)
    grid=assets[0].metadata['expected_native_grid']
    assert grid['width']==360 and grid['height']==3600
    assert grid['x_spacing']==pytest.approx(10/3600)


def test_usgs_release_is_not_an_observation_date(tmp_path):
    from api.dataset_fetch.research.usgs import tile_metadata,describe_selection
    metadata=tmp_path/'tile.xml'
    metadata.write_text('''<metadata><idinfo><citation><citeinfo><title>USGS 1 Arc Second n41w106 20260708</title><pubdate>20260708</pubdate></citeinfo></citation>
      <timeperd><timeinfo><rngdates><begdate>19580101</begdate><enddate>20241016</enddate></rngdates></timeinfo><current>publication date</current></timeperd></idinfo>
      <spref><horizsys><geograph><latres>-0.00027777777778</latres><longres>0.00027777777778</longres><geogunit>Decimal degrees</geogunit></geograph>
      <geodetic><horizdn>North American Datum of 1983</horizdn></geodetic></horizsys><vertdef><altsys><altdatum>North American Vertical Datum of 1988</altdatum><altunits>meters</altunits></altsys></vertdef></spref>
      <dataqual><lineage><srcinfo><title>Different source</title><pubdate>19000101</pubdate></srcinfo></lineage></dataqual></metadata>''')
    details=tile_metadata(metadata,'n41w106')
    asset=Asset(id='tile',url='https://example.org/tile.tif',filename='tile.tif',metadata=details)
    product=registry()['usgs-3dep-30m']
    describe_selection(product,[asset])
    assert product.release_date=='2026-07-08'
    assert product.observation_period['value']=='unknown'
    assert details['source_crs']=='EPSG:4269'
    assert details['provider_time_range']['currentness']=='publication date'
    with pytest.raises(ValueError,match='identity'): tile_metadata(metadata,'n42w107')
    metadata.write_text(metadata.read_text().replace('<altunits>meters','<altunits>feet'))
    with pytest.raises(ValueError,match='units'): tile_metadata(metadata,'n41w106')


def test_usgs_absent_tile_never_substitutes(tmp_path):
    from api.dataset_fetch.research.usgs import discover_seamless
    calls=[]
    def absent(asset):
        calls.append(asset.url)
        raise ValueError('Provider HTTP 404: '+asset.url)
    assets,receipts,findings=discover_seamless(registry()['usgs-3dep-30m'],[(-105.27,40.01,-105.267,40.013)],SimpleNamespace(inspect=absent))
    assert not assets and not receipts and len(findings)==1
    assert calls==['https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/n41w106/USGS_1_n41w106.tif']


def test_eccc_signing_keeps_exact_unsigned_identity(tmp_path,monkeypatch):
    from contextlib import contextmanager
    client=Transport(Store(tmp_path/'store'))
    url='https://data-donnees.az.ec.gc.ca/public//species/product.zip'
    locations=[url+'?sig=secret',url.replace('product.zip','other.zip')+'?sig=secret']
    @contextmanager
    def response(*args,**kw):
        assert kw['params']['path']=='/species/product.zip' and kw['allow_redirects'] is False
        yield SimpleNamespace(status_code=302,headers={'Location':locations.pop(0)})
    monkeypatch.setattr(client,'request',response)
    assert client.signed_url(url)==url+'?sig=secret'
    with pytest.raises(ValueError,match='exact selected'): client.signed_url(url)


def test_file_geodatabase_archive_keeps_complete_container(tmp_path):
    import zipfile
    from osgeo import ogr,osr
    from api.dataset_fetch.research.national import archive_sources
    source=tmp_path/'source.gdb'
    dataset=ogr.GetDriverByName('OpenFileGDB').CreateDataSource(str(source))
    reference=osr.SpatialReference(); reference.ImportFromEPSG(4326)
    layer=dataset.CreateLayer('protected',reference,ogr.wkbPoint)
    feature=ogr.Feature(layer.GetLayerDefn()); feature.SetGeometry(ogr.CreateGeometryFromWkt('POINT (1 1)'))
    assert layer.CreateFeature(feature)==0
    feature=None;layer=None;dataset=None
    archive=tmp_path/'source.zip'
    with zipfile.ZipFile(archive,'w') as output:
        for path in sorted(source.rglob('*')):
            if path.is_file(): output.write(path,path.relative_to(tmp_path).as_posix())
    paths=archive_sources(archive,'eccc_catalogue')
    assert len(paths)==1 and paths[0].endswith('/source.gdb')
    dataset=ogr.Open(paths[0]); assert dataset.GetLayer(0).GetFeatureCount()==1
    dataset=None


def test_coordinate_operation_is_frozen_before_usgs_download(tmp_path):
    from api.dataset_fetch.research.projection import freeze_raster_operations,frozen_operation
    from api.dataset_fetch.research.planning import grid_recipe
    from api.dataset_fetch.research.processing import run_selection
    from api.dataset_fetch.research.contracts import AcquisitionReceipt,now
    from osgeo import gdal,osr
    aoi={'type':'Polygon','coordinates':[[[-105.27,40.01],[-105.267,40.01],[-105.267,40.013],[-105.27,40.013],[-105.27,40.01]]]}
    p=registry()['usgs-3dep-30m'];p.semantics['source_crs']='EPSG:4269'
    recipe=grid_recipe(aoi,'EPSG:32613',p)
    asset=Asset(id='tile',url='https://example.org/tile.tif',filename='tile.tif')
    findings=freeze_raster_operations(p,[asset],recipe,aoi)
    op=frozen_operation(recipe,'EPSG:4269')
    assert op['pipeline'] and op['stated_accuracy_m']==4
    assert any(f.rule=='coordinate-operation-accuracy' for f in findings)
    with pytest.raises(ValueError,match='confirmed plan'): frozen_operation(recipe,'EPSG:4326')
    path=tmp_path/'source.tif'; ds=gdal.GetDriverByName('GTiff').Create(str(path),20,20,1,gdal.GDT_Float32)
    crs=osr.SpatialReference();crs.ImportFromEPSG(4269)
    ds.SetProjection(crs.ExportToWkt());ds.SetGeoTransform((-105.3,.002,0,40.04,0,-.002));ds.GetRasterBand(1).WriteArray(np.ones((20,20)));ds=None
    store=Store(tmp_path/'store');sha,blob=store.retain(path)
    receipt=AcquisitionReceipt(asset_id='tile',source_url=asset.url,sha256=sha,size=blob.stat().st_size,blob=sha,headers={},acquired_at=now())
    selection=PlannedSelection(id='usgs',product=p,parameters={},assets=[asset],recipe=recipe)
    _,results,issues=run_selection(selection,[receipt],SimpleNamespace(aoi=aoi),store,tmp_path/'out')
    assert results[0]['bands'][0]['valid_fraction']==pytest.approx(1)
    assert not any(f.severity=='block' for f in issues)
    assert results[0]['processing_recipe']['coordinate_operations'][0]==op


def test_mixed_crs_mosaic_has_one_resampling_and_stable_precedence(tmp_path):
    from api.dataset_fetch.research.projection import freeze_raster_operations
    from api.dataset_fetch.research.processing import run_selection
    from api.dataset_fetch.research.contracts import AcquisitionReceipt,now
    from osgeo import gdal,osr
    from pyproj import Transformer
    p=registry()['copernicus-glo30'];p.semantics.pop('source_crs')
    recipe={'target_crs':'EPSG:4326','extent':[0,0,10,10],'spacing':[1,1],'resampling':'near','nodata':-9999,'native_export':False}
    assets=[Asset(id=str(i),url=f'https://example.org/{i}.tif',filename=f'{i}.tif',metadata={'source_crs':crs}) for i,crs in enumerate(('EPSG:4326','EPSG:3857'))]
    freeze_raster_operations(p,assets,recipe,AOI)
    store=Store(tmp_path/'store');receipts=[]
    for i,asset in enumerate(assets):
        data=np.full((10,10),i+1,dtype=np.float32)
        if i: data[:,:5]=-9999
        path=raster(tmp_path/f'{i}.tif',data)
        if i:
            ds=gdal.Open(str(path),gdal.GA_Update);ref=osr.SpatialReference();ref.ImportFromEPSG(3857)
            east,north=Transformer.from_crs(4326,3857,always_xy=True).transform(10,10)
            ds.SetProjection(ref.ExportToWkt());ds.SetGeoTransform((0,east/10,0,north,0,-north/10));ds=None
        sha,blob=store.retain(path)
        receipts.append(AcquisitionReceipt(asset_id=asset.id,source_url=asset.url,sha256=sha,size=blob.stat().st_size,blob=sha,headers={},acquired_at=now()))
    selection=PlannedSelection(id='mixed',product=p,parameters={},assets=assets,recipe=recipe)
    outputs,results,issues=run_selection(selection,list(reversed(receipts)),SimpleNamespace(aoi=AOI),store,tmp_path/'out')
    ds=gdal.Open(str(tmp_path/'out'/outputs[0]['file']));data=ds.ReadAsArray();ds=None
    assert np.all(data[:,:5]==1) and np.all(data[:,5:]==2)
    assert len(results[0]['processing_recipe']['coordinate_operations'])==2
    assert not issues and not list((tmp_path/'out').glob('projected-*.tif'))


@pytest.mark.parametrize('status,expected_zone',[('protected_conserved',7),('delisted',8)])
def test_cpcad_status_and_related_comments_are_preserved(tmp_path,status,expected_zone):
    from osgeo import ogr,osr
    import zipfile
    from api.dataset_fetch.research.processing import run_selection
    p=registry()['can-cpcad'];native=tmp_path/'protected.gdb'
    dataset=ogr.GetDriverByName('OpenFileGDB').CreateDataSource(str(native))
    reference=osr.SpatialReference();reference.ImportFromEPSG(4326)
    for zone,name in zip((7,8),p.semantics['layer_by_status'].values()):
        layer=dataset.CreateLayer(name,reference,ogr.wkbPolygon);layer.CreateField(ogr.FieldDefn('ZONE_ID',ogr.OFTInteger))
        feature=ogr.Feature(layer.GetLayerDefn());feature.SetField('ZONE_ID',zone);feature.SetGeometry(ogr.CreateGeometryFromWkt('POLYGON ((1 1,2 1,2 2,1 2,1 1))'));assert layer.CreateFeature(feature)==0
        feature=None;layer=None
    layer=dataset.CreateLayer(p.semantics['auxiliary_join']['table'],geom_type=ogr.wkbNone)
    layer.CreateField(ogr.FieldDefn('ZONE_ID',ogr.OFTInteger));layer.CreateField(ogr.FieldDefn('COMMENT_E',ogr.OFTString))
    feature=ogr.Feature(layer.GetLayerDefn());feature.SetField('ZONE_ID',7);feature.SetField('COMMENT_E','Provider boundary qualification');assert layer.CreateFeature(feature)==0
    feature=None;layer=None;dataset=None
    archive=tmp_path/'source.zip'
    with zipfile.ZipFile(archive,'w') as output:
        for path in sorted(native.rglob('*')):
            if path.is_file():output.write(path,path.relative_to(tmp_path).as_posix())
    store=Store(tmp_path/'store');receipt=Transport(store).retain_bytes(archive.read_bytes(),'https://example.org/source.zip').model_copy(update={'asset_id':'source'})
    selection=PlannedSelection(id='protected',product=p,parameters={'year':'2025','status':status},assets=[Asset(id='source',url='https://example.org/source.zip',filename='source.zip')],recipe={})
    outputs,results,issues=run_selection(selection,[receipt],SimpleNamespace(aoi=AOI,target_crs='EPSG:4326'),store,tmp_path/'out')
    dataset=ogr.Open(str(tmp_path/'out'/outputs[0]['file']));layer=dataset.GetLayer(0)
    assert layer.GetFeatureCount()==1
    properties=json.loads(layer.GetNextFeature().GetField('source_properties'))
    assert properties['ZONE_ID']==expected_zone
    assert properties['zeus_related_source_records']==([{'ZONE_ID':7,'COMMENT_E':'Provider boundary qualification'}] if status=='protected_conserved' else [])
    assert len(results[0]['source_inventories'])==3 and not issues
    layer=None;dataset=None


def climate_selection():
    from api.dataset_fetch.research.climate_acquisition import cds_requests
    product=registry()['era5-single-levels'];params={'start':'2020-01-01','end':'2020-01-02','variables':['total_precipitation','2m_temperature']}
    boxes=[[0,0,.5,.5]]
    requests=cds_requests(product,params,boxes)
    return PlannedSelection(id='climate',product=product,parameters=params,assets=[],recipe={'native_export':True},discovery=[{'cds':{'bbox':boxes,'requests':requests}}])


def test_climate_checkpoints_resume_each_variable_request(tmp_path,monkeypatch):
    from api.dataset_fetch.research import climate_acquisition as module
    selection=climate_selection();store=Store(tmp_path/'store');transport=Transport(store)
    checkpoints={};calls=[];persisted=[]
    def retrieve(item,endpoint,client):
        calls.append(item['id'])
        if len(calls)==2: raise ValueError('simulated provider interruption')
        return [client.retain_bytes(canonical({'id':item['id'],'role':prefix}),endpoint).model_copy(update={'asset_id':prefix+item['id']}) for prefix in ('cds-request:','cds-metadata:','')]
    monkeypatch.setattr(module,'retrieve_one',retrieve)
    with pytest.raises(ValueError,match='interruption'): module.acquire_cds(selection,transport,checkpoints,lambda c:persisted.append(json.loads(canonical(c))))
    assert len(persisted)==1 and len(checkpoints)==1
    first=calls[0]
    receipts=module.acquire_cds(selection,transport,checkpoints,lambda c:persisted.append(json.loads(canonical(c))))
    assert calls.count(first)==1 and len(receipts)==12 and len(checkpoints)==4
    assert all(len(item['request']['variable'])==1 for item in selection.discovery[0]['cds']['requests'])


def test_climate_mixed_variables_zip_and_shuffled_receipts(tmp_path):
    import xarray as xr,zipfile
    from api.dataset_fetch.research.climate import process_climate,VARIABLES
    from api.dataset_fetch.research.netcdf_compat import load_netcdf
    selection=climate_selection();store=Store(tmp_path/'store');client=Transport(store);receipts=[]
    for index,item in enumerate(selection.discovery[0]['cds']['requests']):
        name,units=VARIABLES[item['variable']];path=tmp_path/f'input-{index}.nc'
        accumulated=item['variable']=='total_precipitation'
        dataset=xr.Dataset({name:(('time','latitude','longitude'),np.full((24,2,2),.001 if accumulated else 280.),{'units':units,'GRIB_stepType':'accum' if accumulated else 'instant'}),
            'expver':('time',np.ones(24,dtype=np.int32))},coords={
            'time':('time',np.arange(24),{'units':f"hours since {item['date']} 00:00:00",'calendar':'proleptic_gregorian'}),
            'latitude':('latitude',[.375,.125],{'units':'degrees_north'}),'longitude':('longitude',[.125,.375],{'units':'degrees_east'})})
        dataset.to_netcdf(path,engine='netcdf4')
        if accumulated:
            archive=path.with_suffix('.zip')
            with zipfile.ZipFile(archive,'w') as output: output.write(path,'data/accumulation.nc')
            path=archive
        response=client.retain_bytes(path.read_bytes(),selection.product.endpoint).model_copy(update={'asset_id':item['id']})
        receipts.extend([response,client.retain_bytes(canonical(item),selection.product.endpoint).model_copy(update={'asset_id':'cds-request:'+item['id']}),
            client.retain_bytes(canonical({'content_length':response.size,'content_type':'application/octet-stream'}),selection.product.endpoint).model_copy(update={'asset_id':'cds-metadata:'+item['id']})])
    aoi={'type':'Polygon','coordinates':[[[0,0],[.5,0],[.5,.5],[0,.5],[0,0]]]};reports=[]
    for attempt,rows in enumerate((receipts,list(reversed(receipts)))):
        directory=tmp_path/f'replay-{attempt}';directory.mkdir()
        outputs,results,findings=process_climate(selection,rows,SimpleNamespace(aoi=aoi),store,directory,lambda:False)
        reports.append(digest({'results':results,'findings':[f.model_dump(mode='json') for f in findings]}))
        assert len(outputs)==8 and len(findings)==4
        assert all(f.rule=='climate-time-interpretation' and f.severity=='acknowledgement' for f in findings)
        for output in outputs:
            if output['role']!='native_cf_export':continue
            with load_netcdf().Dataset(directory/output['file']) as value:
                if 'tp' in value.variables: assert value['time'].bounds=='zeus_time_bounds' and np.allclose(value['tp'][:],.001)
                else: assert not hasattr(value['time'],'bounds') and np.all(value['t2m'][:]==280)
    assert reports[0]==reports[1]
    with pytest.raises(ValueError,match='every requested'):process_climate(selection,receipts[1:],SimpleNamespace(aoi=aoi),store,tmp_path/'missing',lambda:False)


def test_osm_nested_relations_preserve_paths_and_dependencies():
    from api.dataset_fetch.research.osm import osm_features
    metadata={'version':1,'timestamp':'2020-01-01T00:00:00Z'}
    roots=[{'type':'relation','id':10,**metadata}]
    elements=[{'type':'relation','id':10,**metadata,'tags':{'route':'road'},'members':[{'type':'relation','ref':20,'role':'forward'}]},
        {'type':'relation','id':20,**metadata,'members':[{'type':'way','ref':30,'role':'main'},{'type':'node','ref':40,'role':'stop'}]},
        {'type':'way','id':30,**metadata,'tags':{'surface':'gravel'},'nodes':[40,50],'geometry':[{'lon':1,'lat':1},{'lon':2,'lat':2}]},
        {'type':'node','id':40,**metadata,'lon':1,'lat':1},{'type':'node','id':50,**metadata,'lon':2,'lat':2}]
    features=osm_features({'elements':elements},roots)
    assert features==osm_features({'elements':list(reversed(elements))},roots)
    assert len(features)==2 and features[0]['id']=='relation/10/member/0/0'
    assert features[0]['properties']['leaf_tags']=={'surface':'gravel'}
    assert [step['role'] for step in features[0]['properties']['relation_ancestry']]==['forward','main']
    with pytest.raises(ValueError,match='dependencies'):osm_features({'elements':elements[:-1]},roots)
    elements[1]['members'].append({'type':'relation','ref':10})
    with pytest.raises(ValueError,match='cyclic'):osm_features({'elements':elements},roots)


def test_osm_frozen_roots_cannot_change_or_disappear():
    from api.dataset_fetch.research.osm import osm_features
    roots=[{'type':'way','id':1,'version':2,'timestamp':'2020-01-01T00:00:00Z'}]
    with pytest.raises(ValueError,match='missing a frozen'):osm_features({'elements':[]},roots)
    with pytest.raises(ValueError,match='changed'):osm_features({'elements':[{'type':'way','id':1,'version':3,'timestamp':'2020-01-01T00:00:00Z'}]},roots)


def test_retaining_an_existing_content_object_does_not_delete_it(tmp_path):
    store=Store(tmp_path/'store');path=tmp_path/'input';path.write_bytes(b'replay source')
    sha,blob=store.retain(path)
    assert store.retain(blob)==(sha,blob) and store.verify_blob(sha).read_bytes()==b'replay source'


def test_legacy_revalidation_never_invents_provenance_or_publishes(tmp_path,monkeypatch):
    from api.dataset_fetch.research import legacy
    project=tmp_path/'project';directory=project/'data/rasters/processed';directory.mkdir(parents=True)
    path=raster(directory/'old.tif',np.ones((10,10)))
    original=file_hash(path)
    monkeypatch.setattr(legacy,'context',lambda name:(SimpleNamespace(project_path=project),AOI))
    store=Store(tmp_path/'store');reports=[]
    for index in range(2):
        reports.append(legacy.revalidate('legacy','data/rasters/processed/old.tif',tmp_path/f'report-{index}',store))
    assert reports[0]['report_hash']==reports[1]['report_hash']
    assert reports[0]['provenance_status']=='legacy_unverified' and reports[0]['original_acquisition_replay']=='unavailable'
    assert reports[0]['results'][0]['bands'][0]['valid_fraction']==pytest.approx(1)
    assert any(rule['rule']=='interpretable-units' and rule['status']=='not_assessed' for rule in reports[0]['results'][0]['rule_results'])
    assert reports[0]['findings'][0]['rule']=='historical-provenance'
    assert not (project/'data/active-generation.json').exists() and file_hash(path)==original
    with pytest.raises(ValueError,match='within the project'):legacy.revalidate('legacy','../../outside.tif',tmp_path/'bad',store)
