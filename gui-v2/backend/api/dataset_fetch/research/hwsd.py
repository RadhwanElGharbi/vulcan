"""FAO HWSD native mapping units and all soil components; no property modelling."""
from collections import Counter,defaultdict
import hashlib
import math
import struct
import time
import zipfile
from pathlib import Path

from .contracts import Asset,Finding,canonical,digest,file_hash
from .hwsd_contract import TABLES
from .store import atomic_json
from .transport import Cancelled

DOC='https://www.fao.org/land-water/resources/tools/databases/hwsd/en'
BASE='https://s3.eu-west-1.amazonaws.com/data.gaezdev.aws.fao.org/HWSD/'
SOURCES={
    'grid':('HWSD2_RASTER.zip',22439811,'55612130174f228f48d0691a2e01ab92150617dfd0d6605e65e3619eb5c123d8'),
    'attributes':('HWSD2_DB.zip',9304008,'6c69e09137a8150f53c8a893fe3e4426d6b5cceb635c00378b54b7d64a9736c0')}
DEPTHS={'D1':(0,20),'D2':(20,40),'D3':(40,60),'D4':(60,80),'D5':(80,100),'D6':(100,150),'D7':(150,200)}
NATIVE_GRID={'width':43200,'height':21600,'x_spacing':1/120,'y_spacing':1/120,'origin':[-180,90],
             'origin_tolerance':1e-10,'bands':1,'nodata':65535,'datatype':'UInt16','scale':1,'offset':0}
SYSTEM_TABLES={'MSysObjects','MSysAccessStorage','MSysNameMap','MSysNavPaneGroupCategories','MSysNavPaneGroups','MSysNavPaneGroupToObjects','MSysNavPaneObjectIDs'}
ATTRIBUTE_UNITS={'TOPDEP':'cm','BOTDEP':'cm','SHARE':'%','COARSE':'% volume','SAND':'% weight','SILT':'% weight','CLAY':'% weight',
    'BULK':'g/cm3','REF_BULK':'g/cm3','ORG_CARBON':'% weight','PH_WATER':'-log(H+)','TOTAL_N':'g/kg','CN_RATIO':'dimensionless ratio',
    'CEC_SOIL':'cmolc/kg','CEC_CLAY':'cmolc/kg','CEC_EFF':'cmolc/kg','TEB':'cmolc/kg','BSAT':'% CECsoil','ALUM_SAT':'% ECEC',
    'ESP':'%','TCARBON_EQ':'% weight','GYPSUM':'% weight','ELEC_COND':'dS/m','AWC':'mm',
    'categorical_attributes':'Provider domain tables and original field metadata retained in the attribute export'}


def products(factory):
    p=factory('fao-hwsd2-native','soil','FAO HWSD v2 native mapping units and soil tables','FAO / IIASA',
        'Exact FAO 2023 MDB and September raster snapshots; SHA-256 pinned','hwsd',BASE,'mapping_unit_id',[DOC],
        native_spacing={'x':1/120,'y':1/120,'units':'degree','nominal_m':1000},
        observation_period={'status':'unknown','reason':'Harmonized legacy soil surveys with different dates; 2023 is the database release, not a simultaneous observation'},
        accuracy={'status':'unknown','reason':'Mapping-unit grid spacing does not establish local survey or property accuracy'},
        attribution='FAO & IIASA. 2023. Harmonized World Soil Database version 2.0. Rome and Laxenburg. https://doi.org/10.4060/cc3823en',
        semantics={'source_crs':'EPSG:4326','native_export':True,'resolution_m':1000,'nodata':65535,
                   'quantity':'Mapping-unit identifier; numerical IDs are not soil-property measurements',
                   'depths_cm':DEPTHS,'attribute_units':ATTRIBUTE_UNITS,'components':'Every sequence, share and depth is retained; no dominant-component choice, interpolation or averaging',
                   'attributes':'Native MDB numeric values and nulls, metadata tables and domain dictionaries; original archives retained',
                   'source_release':'FAO documents a September 2023 v2.01 revision; the linked MDB HTTP modification date is January 2023 and the raster September 2023. Exact bytes are pinned separately.',
                   'reader':'access-parser 0.0.6; every original user table checked against a full independent native ODBC row multiset',
                   'crs_metadata_normalization':'Verify the native PRJ equals WGS 84 ignoring axis order, then assign canonical EPSG:4326 to an internal VRT without changing raster coordinates or values. Preserve the original PRJ and affine grid.',
                   'storage':'Retain both complete global archives (31,743,819 bytes); stream the 1,866,240,000-byte BIL inside its ZIP. Extract the 91,594,752-byte MDB locally. Native bounding-box grid plus linked tables and AOI masks.'})
    p.assessment.findings.extend([
        Finding(id='hwsd-mapping-units',rule='scientific-meaning',severity='acknowledgement',basis=DOC,
            message='The map contains soil mapping-unit IDs, not a continuous soil property. The attribute export retains all components, shares and seven depth layers. Mapping-unit coverage is separate from availability of each property; raw provider nulls and sentinel values are retained.'),
        Finding(id='hwsd-snapshot-version',rule='version-identity',severity='acknowledgement',basis=DOC,
            message='FAO links a January 2023 MDB and a September 2023 raster under the v2/v2.01 overview. ZEUS pins both exact snapshots independently. Their file modification dates are not observation dates. The ISRIC SQLite conversion is not used because exact native layer values differed.'),
        Finding(id='hwsd-global-package',rule='storage-estimate',severity='acknowledgement',basis=DOC,
            message='Acquisition preserves both complete global archives. Native table parsing requires approximately 512 MiB of working memory and 92 MB of temporary disk space; the global BIL is streamed from its archive. Large table subsets are bounded to 100,000 rows.')])
    return [p]


def discover(product,transport):
    assets=[]
    for role,(filename,size,sha) in sorted(SOURCES.items()):
        asset=transport.inspect(Asset(id='hwsd:'+role,url=BASE+filename,filename=filename,role=role,size=size,expected_hash=sha,
            metadata={'source_crs':'EPSG:4326','snapshot_sha256':sha,'checksum_basis':'ZEUS retained source verification 2026-09-17; not a publisher checksum',
                      **({'expected_native_grid':NATIVE_GRID} if role=='grid' else {})}))
        if asset.size!=size:raise ValueError('HWSD source length changed; requalification is required')
        assets.append(asset)
    proof={'native_tables':TABLES,'comparison':'Exact unordered row multisets from Microsoft Access ODBC; original field precision, nulls and strings',
           'origin':'ZEUS-generated native-table verification contract','evidence_urls':[DOC],
           'sources':SOURCES,'verification_scope':'Exact retained snapshots only; not full scientific product qualification'}
    receipt=transport.retain_bytes(canonical(proof),'zeus:qualification/hwsd-native/2026-09-17').model_copy(update={'asset_id':'hwsd-native-contract'})
    return assets,[{'receipt':receipt.model_dump(mode='json'),'role':'native-table-qualification-evidence'}],[]


def archive_members(path,expected,cancelled):
    from .national import inspect_archive
    members=inspect_archive(path,cancelled)
    if members!=sorted(expected):raise ValueError('HWSD native archive dependencies differ from the source contract')
    with zipfile.ZipFile(path) as archive:
        if any(archive.getinfo(name).file_size!=size for name,size in expected.items()):
            raise ValueError('HWSD native archive member length differs from the source contract')


def typed_row_hash(row,types):
    h=hashlib.sha256()
    if len(row)!=len(types):raise ValueError('HWSD native field inventory is incomplete')
    for value,kind in zip(row,types):
        if value is None:h.update(b'\x00')
        elif kind=='String':
            if not isinstance(value,str):raise ValueError('HWSD text value is not interpretable')
            raw=value.encode('utf-8');h.update(b'\x02'+struct.pack('<I',len(raw))+raw)
        else:
            if kind=='Boolean':
                if not isinstance(value,bool):raise ValueError('HWSD Boolean value contradicts its native type')
            elif isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('HWSD native numeric value is invalid')
            if kind=='Byte' and (int(value)!=value or not 0<=value<=255):raise ValueError('HWSD byte value contradicts its native type')
            if kind.startswith('Int') and (int(value)!=value or not -(2**(int(kind[3:])-1))<=value<2**(int(kind[3:])-1)):
                raise ValueError('HWSD integer value contradicts its native type')
            if kind=='Single' and struct.unpack('<f',struct.pack('<f',value))[0]!=value:
                raise ValueError('HWSD floating value differs from native binary32 precision')
            h.update(b'\x01'+struct.pack('<d',value))
    return h.hexdigest()


def page_rows(database,name,cancelled):
    """Bound decoded rows to one native data page, retaining overflow dependencies."""
    table=database.get_table(name);pages=table.table.linked_pages
    try:
        for page in pages:
            if cancelled():raise Cancelled('Cancelled while reading native HWSD table pages')
            table.table.linked_pages=[page];table.parsed_table=defaultdict(list)
            values=table.parse()
            columns=TABLES[name]['columns']
            if not values:continue  # A page can contain only deleted rows; total source row hashes still must match.
            if sorted(values)!=columns or len({len(v) for v in values.values()})!=1:
                raise ValueError('HWSD native page lost fields or row values')
            yield from zip(*(values[c] for c in columns))
    finally:
        table.table.linked_pages=pages


def read_tables(path,unit_ids,cancelled):
    from access_parser import AccessParser
    started=time.monotonic()
    def check():
        if time.monotonic()-started>1200:raise ValueError('HWSD table verification exceeded its 20-minute deadline')
        return cancelled()
    database=AccessParser(str(path))
    if set(database.catalog)!=set(TABLES)|SYSTEM_TABLES:raise ValueError('HWSD native database table inventory changed')
    tables={};evidence=[];selected_count=0
    for name,contract in sorted(TABLES.items(),key=lambda item:(item[1]['rows'],item[0])):
        counts=Counter();selected=[];columns=contract['columns']
        unit_column=columns.index('HWSD2_SMU_ID') if name in ('HWSD2_SMU','HWSD2_LAYERS') else None
        total=0
        for row in page_rows(database,name,check):
            total+=1
            if total>contract['rows']:raise ValueError('HWSD table exceeds its verified row inventory')
            counts[typed_row_hash(row,contract['native_types'])]+=1
            if unit_column is None or row[unit_column] in unit_ids:
                selected_count+=1
                if selected_count>100000:raise ValueError('HWSD attribute subset exceeds 100,000 rows; reduce the AOI')
                selected.append(list(row))
        identity=hashlib.sha256(''.join(k+'\t'+str(v)+'\n' for k,v in sorted(counts.items())).encode()).hexdigest()
        if total!=contract['rows'] or identity!=contract['sha256']:
            raise ValueError('HWSD native table content differs from the independent source verification: '+name)
        # Exact row order is canonical; nulls/strings/numbers remain distinct.
        selected.sort(key=canonical)
        tables[name]={'columns':columns,'native_types':contract['native_types'],'rows':selected}
        evidence.append({'table':name,'source_rows':total,'exported_rows':len(selected),'source_content_sha256':identity,'status':'passed'})
    return tables,evidence


def table_records(table):
    return [dict(zip(table['columns'],row)) for row in table['rows']]


def native_vrt(source,target):
    from osgeo import gdal
    from pyproj import CRS
    dataset=gdal.Open(source)
    if dataset is None or not dataset.GetProjection() or not CRS.from_wkt(dataset.GetProjection()).equals(CRS.from_epsg(4326),ignore_axis_order=True):
        raise ValueError('HWSD native PRJ does not establish WGS 84')
    original=dataset.GetProjection();grid=list(dataset.GetGeoTransform())
    vrt=gdal.Translate(str(target),dataset,format='VRT',outputSRS='EPSG:4326')
    if vrt is None:raise ValueError('HWSD native BIL cannot be interpreted')
    vrt.GetRasterBand(1).SetUnitType('mapping_unit_id');vrt=None;dataset=None
    return {'original_wkt':original,'original_affine':grid,'operation':'Canonical WGS 84 axis metadata assignment; no coordinate changes or resampling'}


def validate_links(tables,unit_ids):
    smu=table_records(tables['HWSD2_SMU']);layers=table_records(tables['HWSD2_LAYERS'])
    ids=[r['HWSD2_SMU_ID'] for r in smu]
    if len(ids)!=len(set(ids)) or set(ids)!=set(unit_ids):raise ValueError('HWSD grid IDs do not reconcile with mapping-unit attributes')
    components={};keys=set()
    for row in layers:
        unit,sequence,depth=row['HWSD2_SMU_ID'],row['SEQUENCE'],row['LAYER']
        if unit not in unit_ids or not isinstance(sequence,int) or sequence<1:raise ValueError('HWSD component identity is invalid')
        key=(unit,sequence,depth)
        if key in keys:raise ValueError('HWSD component/depth identity is duplicated')
        keys.add(key)
        if depth not in DEPTHS or (row['TOPDEP'],row['BOTDEP'])!=DEPTHS[depth]:raise ValueError('HWSD native depth bounds are invalid')
        if not isinstance(row['SHARE'],int) or not 0<=row['SHARE']<=100:raise ValueError('HWSD component share is invalid')
        components.setdefault((unit,sequence),{})[depth]=row['SHARE']
    if {k[0] for k in components}!=set(unit_ids):raise ValueError('HWSD selected mapping unit has no soil components')
    for shares in components.values():
        if set(shares)!=set(DEPTHS) or len(set(shares.values()))!=1:raise ValueError('HWSD soil component has missing depths or inconsistent shares')
    totals={str(unit):sum(shares['D1'] for (identifier,_),shares in components.items() if identifier==unit) for unit in sorted(unit_ids)}
    return {'mapping_units':len(unit_ids),'components':len(components),'depth_records':len(layers),'component_share_totals_percent':totals,
            'attribute_nulls':{field:sum(row[field] is None for row in layers) for field in tables['HWSD2_LAYERS']['columns']},
            'attribute_minus9_values':{field:sum(row[field]==-9 for row in layers) for field in tables['HWSD2_LAYERS']['columns']}}


def process(selection,inputs,plan,store,workdir,cancelled):
    import numpy as np
    import shutil
    from osgeo import gdal
    from .processing import run_selection
    paths={asset.role:(asset,Path(path)) for asset,path in inputs}
    if set(paths)!=set(SOURCES):raise ValueError('HWSD acquisition lacks a mandatory native archive')
    for role,(asset,path) in paths.items():
        if file_hash(path)!=SOURCES[role][2]:raise ValueError('HWSD source changed from the qualified snapshot')
    archive_members(paths['grid'][1],{'HWSD2.bil':1866240000,'HWSD2.hdr':338,'HWSD2.prj':146,'HWSD2.stx':64},cancelled)
    archive_members(paths['attributes'][1],{'HWSD2.mdb':91594752},cancelled)
    if shutil.disk_usage(workdir).free<128*1024**2:raise OSError('Insufficient space to decode native HWSD attributes')
    mdb=workdir/'HWSD2.mdb'
    with zipfile.ZipFile(paths['attributes'][1]) as archive,archive.open('HWSD2.mdb') as source,mdb.open('wb') as target:
        while block:=source.read(1024**2):
            if cancelled():raise Cancelled('Cancelled during HWSD database materialization')
            target.write(block)
    # Reuse the native-grid subset and complete raster validator. The BIL and
    # every sidecar remain inside the preserved ZIP; no global expansion.
    source='/vsizip/'+paths['grid'][1].as_posix()+'/HWSD2.bil'
    vrt=workdir/'native.vrt'
    normalization=native_vrt(source,vrt)
    native=selection.model_copy(deep=True);native.product.adapter='hwsd_prepared'
    native.assets=[paths['grid'][0].model_copy(update={'filename':'native.vrt'})]
    native.assets[0].metadata={**native.assets[0].metadata,'crs_metadata_normalization':normalization}
    # Internal VRT is created from verified native inputs, never downloaded.
    # A separate path avoids treating native table files as raster sources.
    outputs,results,findings=run_selection(native,[],plan,store,workdir/'grid',cancelled,prepared_inputs=[(native.assets[0],str(vrt))])
    grid=workdir/'grid'/outputs[0]['file'];dataset=gdal.Open(str(grid));units=set()
    for y in range(0,dataset.RasterYSize,256):
        if cancelled():raise Cancelled('Cancelled during HWSD mapping-unit inventory')
        values=dataset.GetRasterBand(1).ReadAsArray(0,y,dataset.RasterXSize,min(256,dataset.RasterYSize-y))
        units.update(int(v) for v in np.unique(values) if v!=65535)
    dataset=None
    tables,table_evidence=read_tables(mdb,units,cancelled)
    link_evidence=validate_links(tables,units)
    value={'schema_version':'zeus.hwsd-native-tables/1','mapping_unit_ids':sorted(units),'tables':tables,
        'interpretation':'Native bounding-box grid mapping units; all soil components and depths. Original numeric precision, nulls and sentinel values preserved. No property interpolation, aggregation or dominant-component selection.',
        'metadata_field_normalization':'Metadata FIELD AWC has a trailing space in the source; retained verbatim. NSC is an undocumented text attribute; retained without assigning units.',
        'source_sha256':{role:SOURCES[role][2] for role in SOURCES}}
    table_path=workdir/(selection.id+'-attributes.json');atomic_json(table_path,value)
    science=digest(value)
    checks={'selection_id':selection.id,'kind':'table','artifact':table_path.name,'scientific_hash':science,'tables':table_evidence,'links':link_evidence,
            'rule_results':[{'rule':rule,'status':'passed','basis':DOC if rule=='mapping-unit-component-depth-reconciliation' else 'ZEUS policy: independent native row multiset, exact retained snapshot'} for rule in ('complete-native-table-scan','exact-native-field-values','mapping-unit-component-depth-reconciliation')]}
    results.append(checks)
    nonunit={str(k):v for k,v in link_evidence['component_share_totals_percent'].items() if v!=100}
    if nonunit:findings.append(Finding(id=selection.id+':component-shares',rule='component-share-total',severity='acknowledgement',basis=DOC,
        message='Some provider component shares do not total 100%; values are preserved without renormalization.',evidence={'totals_percent':nonunit}))
    for item in outputs:
        item['product']=selection.product.model_dump(mode='json')
        for key in ('file','gap_mask','extent_gap'):
            if item.get(key):item[key]='grid/'+item[key]
    for result in results[:-1]:
        for key in ('artifact','gap_mask'):
            if result.get(key):result[key]='grid/'+result[key]
    outputs.append({'id':selection.id+'-attributes','name':selection.product.name+' — native attributes','category':'soil','kind':'table','role':'native_mapping_unit_components',
        'file':table_path.name,'sha256':file_hash(table_path),'scientific_hash':science,'product':selection.product.model_dump(mode='json'),'parameters':selection.parameters,
        'recipe':{'operation':'Decode original MDB pages; verify all native rows; select every linked component/depth by native bounding-box mapping-unit IDs','native_values':'unchanged'}})
    mdb.unlink()
    return outputs,results,findings
