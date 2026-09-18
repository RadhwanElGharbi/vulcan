"""OS Terrain 50 native grid packages, metadata and deterministic tile selection."""
import io
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from pyproj import CRS,Transformer
from shapely.geometry import shape,box
from shapely.ops import transform

from .contracts import Asset,Finding,file_hash
from .national import inspect_archive
from .projection import resolve_operation

DOC='https://docs.os.uk/os-downloads/products/land-and-terrain-portfolio/os-terrain-50/os-terrain-50-technical-specification/grid-data'
NS={'gmd':'http://www.isotc211.org/2005/gmd','gco':'http://www.isotc211.org/2005/gco','gmx':'http://www.isotc211.org/2005/gmx',
    'gml':'http://www.opengis.net/gml/3.2','os':'http://namespaces.ordnancesurvey.co.uk/elevation/grid/v1.0'}


def tile_bounds(tile):
    if not re.fullmatch('[A-HJ-Z]{2}[0-9]{2}',tile):raise ValueError('Invalid OS national grid tile identity')
    letters='ABCDEFGHJKLMNOPQRSTUVWXYZ'
    first,second=map(letters.index,tile[:2])
    e100=((first-2)%5)*5+second%5
    n100=19-(first//5)*5-second//5
    if not 0<=e100<=6 or not 0<=n100<=12:raise ValueError('OS grid tile lies outside Great Britain grid coverage')
    x=e100*100000+int(tile[2])*10000;y=n100*100000+int(tile[3])*10000
    return [x,y,x+10000,y+10000]


def xml(raw):
    if len(raw)>1024**2 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Unsafe or oversized OS metadata XML')
    return ET.fromstring(raw)


def describe_tile(raw,tile):
    """Read all required dependencies, without following GML/XML remote links."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist()
        required={tile+'.asc',tile+'.prj',tile+'.gml','Metadata_'+tile+'.xml',tile+'.asc.aux.xml'}
        if set(names)!=required or len(names)!=len(required) or any(i.file_size>2*1024**2 for i in archive.infolist()):
            raise ValueError('OS tile dependency inventory is incomplete or unexpected')
        if archive.testzip():raise ValueError('OS tile archive CRC failure')
        files={name:archive.read(name) for name in sorted(names)}
    from hashlib import sha256
    metadata=xml(files['Metadata_'+tile+'.xml']);gml=xml(files[tile+'.gml'])
    def text(path):return metadata.findtext(path,default='',namespaces=NS).strip()
    if text('gmd:fileIdentifier/gco:CharacterString')!='OSTerrain50.'+tile or text('.//gmd:collectiveTitle/gco:CharacterString')!='OS Terrain 50':
        raise ValueError('OS metadata does not establish the selected tile/product identity')
    prj=CRS.from_wkt(files[tile+'.prj'].decode('utf-8')).to_json_dict()
    scale=next((p for p in prj.get('conversion',{}).get('parameters',[]) if p.get('id',{}).get('code')==8805),None)
    if scale is None or abs(scale['value']-.9996012717)>5e-10:
        raise ValueError('OS tile PRJ scale contradicts the documented horizontal CRS')
    original_scale=scale['value'];scale['value']=.9996012717
    if not CRS.from_json_dict(prj).equals(CRS.from_epsg(27700),ignore_axis_order=True):
        raise ValueError('OS tile PRJ contradicts the documented horizontal CRS')
    references=[a.get('{http://www.w3.org/1999/xlink}href') for a in metadata.findall('gmd:referenceSystemInfo//gmx:Anchor',NS)]
    if 'urn:ogc:def:crs:EPSG::27700' not in references:raise ValueError('OS metadata omits the horizontal CRS')
    vertical=[r for r in references if r!='urn:ogc:def:crs:EPSG::27700']
    if len(vertical)!=1:raise ValueError('OS vertical reference is missing or ambiguous')
    vcrs=CRS.from_user_input(vertical[0])
    if not vcrs.is_vertical or len(vcrs.axis_info)!=1 or vcrs.axis_info[0].unit_name!='metre':raise ValueError('OS vertical CRS/height units cannot be interpreted')
    bounds=tile_bounds(tile)
    envelope=gml.find('gml:boundedBy/gml:Envelope',NS)
    if envelope is None or envelope.get('srsName')!='urn:ogc:def:crs:EPSG::27700':raise ValueError('OS GML horizontal CRS is missing')
    lower=[float(v) for v in envelope.findtext('gml:lowerCorner',namespaces=NS).split()]
    upper=[float(v) for v in envelope.findtext('gml:upperCorner',namespaces=NS).split()]
    if lower+upper!=bounds:raise ValueError('OS GML extent contradicts the tile identity')
    if gml.findtext('.//gml:fileReference',namespaces=NS)!=tile+'.asc' or gml.findtext('.//os:surfaceType',namespaces=NS)!='DTM' or gml.findtext('.//os:propertyType',namespaces=NS)!='height':
        raise ValueError('OS GML scientific value/dependency contract changed')
    if gml.findtext('.//gml:low',namespaces=NS)!='0 0' or gml.findtext('.//gml:high',namespaces=NS)!='199 199' or [n.text for n in gml.findall('.//gml:offsetVector',NS)]!=['0 50','50 0']:
        raise ValueError('OS GML grid dimensions or spacing changed')
    lines=files[tile+'.asc'].decode('ascii').splitlines()
    lines=[line for line in lines if line.strip()]
    expected={'ncols':200,'nrows':200,'xllcorner':bounds[0],'yllcorner':bounds[1],'cellsize':50}
    header={}
    for line in lines[:5]:
        key,value=line.split();header[key.lower()]=float(value)
    if header!=expected or len(lines)!=205:raise ValueError('OS ASCII header or row inventory contradicts the native tile grid')
    # A full 40,000-value scan prevents GDAL from accepting missing/trailing rows.
    for line in lines[5:]:
        values=line.split()
        if len(values)!=200 or any(not math.isfinite(float(v)) for v in values):raise ValueError('OS ASCII grid has truncated or nonfinite native values')
    start=metadata.findtext('.//gml:beginPosition',default='',namespaces=NS)
    end=metadata.findtext('.//gml:endPosition',default='',namespaces=NS)
    if start and end and date.fromisoformat(start)>date.fromisoformat(end):raise ValueError('OS flying dates are reversed')
    record={'tile':tile,'source_crs':'EPSG:27700','vertical_reference':vcrs.to_string(),'vertical_name':vcrs.name,'units':'m','surface':'DTM',
        'flying_start':start or None,'flying_end':end or None,'processing_date':text('.//gmd:CI_Date/gmd:date/gco:Date') or None,
        'metadata_date':text('gmd:dateStamp/gco:DateTime') or None,'edition':text('.//gmd:edition/gco:CharacterString'),
        'lineage':text('.//gmd:LI_Lineage/gmd:statement/gco:CharacterString'),
        'prj_normalization':{'provider_scale':original_scale,'metadata_authority_scale':.9996012717,'absolute_tolerance':5e-10,
                             'basis':'Explicit EPSG:27700 in publisher GML/ISO metadata; PRJ scale rounded to nine decimals. Every other CRS parameter must agree.',
                             'operation':'Assign authoritative CRS to a local VRT without changing coordinates or resampling values'},
        'dependencies':{name:sha256(data).hexdigest() for name,data in files.items()},
        'expected_native_grid':{'width':200,'height':200,'x_spacing':50,'y_spacing':50,'origin':[bounds[0],bounds[3]],'bands':1,'nodata':None,'scale':1,'offset':0}}
    return record,files


def discover(product,aoi,transport):
    metadata,record=transport.json(product.endpoint)
    if metadata.get('id')!='Terrain50' or not re.fullmatch(r'\d{4}-\d{2}',metadata.get('version','')):raise ValueError('OS Terrain 50 release identity is unavailable')
    product.version=metadata['version']
    rows,downloads=transport.json(metadata['downloadsUrl'],allow_list=True)
    matches=[r for r in rows if r.get('format')=='ASCII Grid and GML (Grid)' and r.get('area')=='GB']
    if len(matches)!=1:raise ValueError('OS Terrain 50 native grid package is missing or ambiguous')
    row=matches[0]
    if row.get('fileName')!='terr50_gagg_gb.zip' or not re.fullmatch('[0-9a-f]{32}',row.get('md5','')):raise ValueError('OS grid archive identity/checksum is invalid')
    entry=Asset(id='os-terrain50:'+product.version,url=row['url'],filename=row['fileName'],size=row['size'],expected_hash=row['md5'],hash_algorithm='md5')
    entry=transport.inspect(entry)
    if entry.size!=row['size']:raise ValueError('OS archive length contradicts the download inventory')
    # The publisher supplies one national archive, with no separate index API.
    # Retain it during discovery so actual tile dates/vertical references can be
    # reviewed. Execution still revalidates its exact frozen identity.
    receipt=transport.download(entry.model_copy(update={'id':'os-terrain50:retained-index'}))
    path=transport.store.verify_blob(receipt.sha256)
    members=inspect_archive(path,transport.cancelled)
    operation=resolve_operation(4326,27700,aoi,always_xy=True)
    geometry=transform(Transformer.from_pipeline(operation['pipeline']).transform,shape(aoi)).buffer(100)
    selected=[];seen=set()
    with zipfile.ZipFile(path) as archive:
        for name in members:
            if name=='licence.txt':continue
            match=re.fullmatch(r'data/([a-hj-z]{2})/([a-hj-z]{2}[0-9]{2})_OST50GRID_([0-9]{8})\.zip',name)
            if not match:raise ValueError('Unexpected OS national archive member')
            tile=match[2].upper()
            if tile in seen or match[1]!=match[2][:2]:raise ValueError('OS tile inventory has duplicate or contradictory identities')
            seen.add(tile)
            if not box(*tile_bounds(tile)).intersects(geometry):continue
            if archive.getinfo(name).file_size>4*1024**2:raise ValueError('OS nested tile exceeds its input limit')
            details,_=describe_tile(archive.read(name),tile)
            selected.append({'member':name,**details})
    if not selected:raise ValueError('No OS Terrain 50 tiles intersect the buffered AOI')
    references=sorted({r['vertical_reference'] for r in selected})
    if len(references)!=1:raise ValueError('Selected OS tiles use different vertical references; separate selections are required')
    entry.metadata={'tiles':selected,'source_crs':'EPSG:27700','archive_sha256':receipt.sha256,'index_aoi_operation':operation}
    product.observation_period={'tiles':[{'tile':r['tile'],'start':r['flying_start'],'end':r['flying_end'],'lineage':r['lineage']} for r in selected],
        'basis':'Retained publisher tile flying dates; partial updates are identified in each lineage statement'}
    product.semantics.update({'vertical_reference':references[0],'release_month':product.version,'source_preparation':'Native 200 x 200 ASCII grids decoded as Float32. Assign EPSG:27700 from GML after reconciling rounded PRJ scale within 5e-10; no coordinate changes. 100 m index buffer for bilinear edges; one analytical resampling per source. Provider styling statistics are retained but not used.'})
    findings=[]
    if any(not r['flying_start'] or not r['flying_end'] for r in selected):
        findings.append(Finding(id='os-flying-dates',rule='observation-age',severity='acknowledgement',message='Some selected OS tile flying dates are unknown in retained metadata.',basis=DOC))
    return [entry],[record,downloads,{'receipt':receipt.model_dump(mode='json'),'role':'national-grid-index','inventory_tiles':len(seen)}],findings


def prepare(entry,path,workdir,cancelled):
    from osgeo import gdal
    from .transport import Cancelled
    if file_hash(Path(path))!=entry.metadata['archive_sha256']:raise ValueError('OS archive differs from the reviewed inventory')
    result=[]
    with zipfile.ZipFile(path) as archive:
        for expected in entry.metadata['tiles']:
            if cancelled():raise Cancelled('Cancelled during native OS tile preparation')
            tile=expected['tile'];actual,files=describe_tile(archive.read(expected['member']),tile)
            if actual!={k:v for k,v in expected.items() if k!='member'}:raise ValueError('OS tile metadata changed after review')
            folder=workdir/('native-'+tile);folder.mkdir()
            for suffix in ('.asc','.prj'):
                (folder/(tile+suffix)).write_bytes(files[tile+suffix])
            vrt=folder/(tile+'.vrt')
            dataset=gdal.Translate(str(vrt),str(folder/(tile+'.asc')),format='VRT',outputSRS='EPSG:27700')
            if dataset is None:raise ValueError('OS authoritative CRS could not be attached to the retained native grid')
            dataset.GetRasterBand(1).SetUnitType('m');dataset.FlushCache();dataset=None
            native=entry.model_copy(update={'id':entry.id+':'+tile,'metadata':actual})
            result.append((native,str(vrt)))
    return result
