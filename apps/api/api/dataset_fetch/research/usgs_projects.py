"""USGS project DEMs: reconcile the official project index and every tile metadata object."""
from __future__ import annotations
import re
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit,parse_qs
from .contracts import Asset,Finding,canonical

BUCKET='https://prd-tnm.s3.amazonaws.com/'
INDEX='https://index.nationalmap.gov/arcgis/rest/services/3DEPElevationIndex/MapServer/18'
NS={'s':'http://s3.amazonaws.com/doc/2006-03-01/'}
BASIS='https://www.usgs.gov/ngp-standards-and-specifications/3dep-product-metadata'


def list_objects(prefix,transport,snapshots):
    objects={};token=None;visited=set()
    while True:
        query={'list-type':2,'prefix':prefix,'max-keys':1000}
        if token: query['continuation-token']=token
        with transport.request('GET',BUCKET,params=query) as response:
            content=b''.join(transport.chunks(response))
            receipt=transport.retain_bytes(content,BUCKET,response.headers)
        snapshots.append({'receipt':receipt.model_dump(mode='json'),'request':{'url':BUCKET,'params':query}})
        root=ET.fromstring(content)
        if root.tag!='{'+NS['s']+'}ListBucketResult' or root.findtext('s:Prefix',namespaces=NS)!=prefix:
            raise ValueError('USGS S3 listing has the wrong object inventory identity')
        rows=root.findall('s:Contents',NS)
        if int(root.findtext('s:KeyCount',namespaces=NS))!=len(rows): raise ValueError('USGS S3 page count mismatch')
        for row in rows:
            key=row.findtext('s:Key',namespaces=NS)
            if not key or not key.startswith(prefix) or '..' in PurePosixPath(key).parts or key in objects:
                raise ValueError('USGS S3 returned an invalid or duplicate object identity')
            objects[key]={'size':int(row.findtext('s:Size',namespaces=NS)),'etag':row.findtext('s:ETag',namespaces=NS)}
        if len(objects)>20_000: raise ValueError('USGS project inventory exceeds the 20,000-object limit')
        truncated=root.findtext('s:IsTruncated',namespaces=NS)
        if truncated=='false': return objects
        following=root.findtext('s:NextContinuationToken',namespaces=NS)
        if truncated!='true' or not rows or not following or following in visited:
            raise ValueError('USGS S3 pagination is truncated or cyclic')
        token=following;visited.add(token)


def project_crs(root,abstract,stem):
    """Resolve declarations by meaning, not one provider's display spelling."""
    from pyproj import CRS
    from pyproj.exceptions import CRSError
    def text(path):
        values={n.text.strip() for n in root.findall(path) if n.text and n.text.strip()}
        if len(values)>1:raise ValueError(f'USGS tile {stem}: conflicting coordinate metadata at {path}')
        return next(iter(values),'')
    grid=text('spref/horizsys/planar/gridsys/gridsysn')
    zone_text=text('spref/horizsys/planar/gridsys/utm/utmzone')
    datum=text('spref/horizsys/geodetic/horizdn')
    normalized=re.sub(r'[_\s]+',' ',grid).strip()
    parsed=None
    projection_names=('utm','universal transverse mercator','transverse mercator')
    if normalized and normalized.lower() not in projection_names:
        try:parsed=CRS.from_user_input(normalized).to_2d()
        except CRSError:pass
    zone=None
    if zone_text:
        try:
            value=float(zone_text)
            if not math.isfinite(value) or value!=int(value):raise ValueError()
            zone=int(value)
        except ValueError:raise ValueError(f'USGS tile {stem}: invalid UTM zone {zone_text!r}')
    if parsed and parsed.utm_zone:
        if not parsed.utm_zone.endswith('N'):raise ValueError(f'USGS tile {stem}: unexpected southern UTM declaration')
        declared=int(parsed.utm_zone[:-1])
        if zone is not None and zone!=declared:raise ValueError(f'USGS tile {stem}: CRS name and UTM zone contradict each other')
        zone=declared
    if zone is None or not 1<=zone<=60:
        raise ValueError(f'USGS tile {stem}: cannot resolve UTM zone from {grid!r} / {zone_text!r}')
    datum_input=datum or abstract
    compact=re.sub(r'[^a-z0-9]','',datum_input.lower())
    datum_name=None
    if 'nad83' in compact or 'northamericandatumof1983' in compact or 'northamericandatum1983' in compact:
        datum_name='NAD83'
        realization=re.search(r'NAD\s*83\s*\(\s*([^)]*)\)',datum_input,re.I)
        realization_text=re.sub(r'[^a-z0-9]','',(realization.group(1) if realization else datum).lower())
        for marker,name in [('2011','NAD83(2011)'),('harn','NAD83(HARN)'),('nsrs2007','NAD83(NSRS2007)')]:
            # Realizations must be explicit datum declarations, not survey years.
            if marker in realization_text:datum_name=name
        if realization and datum_name=='NAD83':
            raise ValueError(f'USGS tile {stem}: datum realization {realization.group(1)!r} needs explicit CRS support')
    elif datum and ('wgs84' in compact or 'worldgeodeticsystem1984' in compact):datum_name='WGS 84'
    if datum and datum_name is None:
        raise ValueError(f'USGS tile {stem}: horizontal datum {datum!r} is not yet interpretable')
    if parsed is not None:
        if not parsed.utm_zone:raise ValueError(f'USGS tile {stem}: CRS {grid!r} contradicts the UTM declaration')
        if not datum and datum_name=='NAD83' and not (parsed.datum.name.startswith('NAD83') or parsed.datum.name=='North American Datum 1983'):
            raise ValueError(f'USGS tile {stem}: CRS name contradicts the abstract horizontal datum')
        if datum or (datum_name and datum_name!='NAD83'):
            expected=CRS.from_user_input(f'{datum_name} / UTM zone {zone}N')
            if not parsed.equals(expected):raise ValueError(f'USGS tile {stem}: CRS name and horizontal datum contradict each other')
        crs=parsed
    else:
        if normalized.lower() not in projection_names or not datum_name:
            raise ValueError(f'USGS tile {stem}: cannot resolve CRS from grid {grid!r}, zone {zone_text!r}, datum {datum!r}; more source metadata is needed')
        crs=CRS.from_user_input(f'{datum_name} / UTM zone {zone}N')
    expected={'sfctrmer':0.9996,'longcm':zone*6-183,'latprjo':0,'feast':500000,'fnorth':0}
    parameters={}
    for key,value in expected.items():
        raw=text('spref/horizsys/planar/gridsys/utm/transmer/'+key)
        if raw:
            try:actual=float(raw)
            except ValueError:raise ValueError(f'USGS tile {stem}: invalid projection parameter {key}={raw!r}')
            if not math.isfinite(actual) or not math.isclose(actual,value,rel_tol=0,abs_tol=1e-8):
                raise ValueError(f'USGS tile {stem}: projection parameter {key}={raw} contradicts UTM zone {zone}N')
            parameters[key]=actual
    if any(not math.isclose(axis.unit_conversion_factor,1) for axis in crs.axis_info):
        raise ValueError(f'USGS tile {stem}: native CRS does not use metres')
    return crs.to_string(),crs.datum.name,{'grid_declaration':grid,'zone_declaration':zone_text,'datum_declaration':datum or None,
        'projection_parameters':parameters,'resolved_crs':crs.to_string(),'method':'Pinned PROJ CRS interpretation and structured UTM parameter reconciliation'}


def project_metadata(path,stem,boxes=None):
    root=ET.parse(path).getroot()
    def field(path):
        nodes=root.findall(path)
        if len(nodes)!=1 or not nodes[0].text: raise ValueError('USGS project metadata lacks a unique field: '+path)
        return nodes[0].text.strip()
    bounds=[float(field('idinfo/spdom/bounding/'+name)) for name in ('westbc','southbc','eastbc','northbc')]
    if not all(math.isfinite(v) for v in bounds) or not (-180<=bounds[0]<bounds[2]<=180 and -90<=bounds[1]<bounds[3]<=90):
        raise ValueError('USGS tile metadata has invalid geographic coverage')
    if boxes is not None:
        from shapely.geometry import box
        if not any(box(*bounds).intersects(box(*aoi_bounds)) for aoi_bounds in boxes):return None
    title=field('idinfo/citation/citeinfo/title')
    # The title's separators changed in older releases. The tile token must agree.
    token=re.search(r'x\d+y\d+',stem)
    if not token or token.group() not in title or not re.search(r'(1 Meter|one meter)',title,re.I):
        raise ValueError('USGS project tile metadata contradicts the selected one-meter object')
    abstract=field('idinfo/descript/abstract')
    if 'All bare earth elevation values are in meters' not in abstract or 'North American Vertical Datum of 1988 (NAVD88)' not in abstract:
        raise ValueError('USGS project metadata does not establish mandatory elevation units and vertical reference')
    source_crs,horizontal_datum,crs_evidence=project_crs(root,abstract,stem)
    def date(path): return datetime.strptime(field(path),'%Y%m%d').date().isoformat()
    period={'start':date('idinfo/timeperd/timeinfo/rngdates/begdate'),'end':date('idinfo/timeperd/timeinfo/rngdates/enddate'),'currentness':field('idinfo/timeperd/current')}
    if period['start']>period['end']: raise ValueError('USGS project time range is reversed')
    return {'title':title,'publication_date':date('idinfo/citation/citeinfo/pubdate'),'provider_time_range':period,
            'source_crs':source_crs,'horizontal_datum':horizontal_datum,'crs_resolution':crs_evidence,
            'vertical_datum':'NAVD88','vertical_units':'meters','footprint':bounds,
            'native_spacing':{'x':1,'y':1,'units':'m','basis':'Standard one-meter product identity in retained tile abstract'},
            'expected_native_grid':{'width':int(field('spdoinfo/rastinfo/colcount')),'height':int(field('spdoinfo/rastinfo/rowcount')),'x_spacing':1,'y_spacing':1,
                'spacing_absolute_tolerance':1e-7,'tolerance_units':'m','tolerance_basis':'ZEUS native-grid policy: at most 0.0000001 m per nominal 1 m step (approximately 1 mm across a 10 km tile); actual affine coefficients are retained'},
            'access_method':'USGS project index and complete S3 tile-metadata inventory'}


def discover_projects(product,boxes,transport):
    from shapely.geometry import box
    snapshots=[];projects={}
    metadata,record=transport.json(INDEX,params={'f':'json'});snapshots.append(record)
    oid=metadata.get('objectIdField') or next((f['name'] for f in metadata.get('fields',[]) if f.get('type')=='esriFieldTypeOID'),None)
    if not oid: raise ValueError('USGS project index lacks an object identity field')
    for bounds in boxes:
        common={'f':'json','where':'1=1','geometry':','.join(map(str,bounds)),'geometryType':'esriGeometryEnvelope','inSR':4326,'spatialRel':'esriSpatialRelIntersects'}
        count,record=transport.json(INDEX+'/query',params={**common,'returnCountOnly':'true'});snapshots.append(record)
        ids,record=transport.json(INDEX+'/query',params={**common,'returnIdsOnly':'true'});snapshots.append(record)
        expected=ids.get('objectIds')
        if expected is None and count.get('count')==0: expected=[]
        if not isinstance(expected,list) or ids.get('exceededTransferLimit') or len(set(expected))!=len(expected) or count.get('count')!=len(expected) or len(expected)>2048:
            raise ValueError('USGS project ID inventory is incomplete, changing or oversized')
        returned=[]
        for offset in range(0,len(expected),100):
            batch=sorted(expected)[offset:offset+100]
            payload,record=transport.json(INDEX+'/query',params={'f':'json','objectIds':','.join(map(str,batch)),'outFields':'*','returnGeometry':'false'});snapshots.append(record)
            rows=payload.get('features')
            if not isinstance(rows,list) or payload.get('exceededTransferLimit') or sorted(f['attributes'][oid] for f in rows)!=batch:
                raise ValueError('USGS project features do not reconcile with the frozen ID inventory')
            for row in rows:
                value=row['attributes'];key=value[oid];returned.append(key)
                if key in projects and canonical(projects[key])!=canonical(value): raise ValueError('USGS project index changed between AOI queries')
                projects[key]=value
        if len(returned)!=len(expected): raise ValueError('USGS project index returned a truncated inventory')
    assets=[];metadata_count=0
    for _,project in sorted(projects.items()):
        link=urlsplit(project.get('product_link',''))
        prefixes=parse_qs(link.query).get('prefix',[])
        if link.hostname!='prd-tnm.s3.amazonaws.com' or len(prefixes)!=1 or not prefixes[0].startswith('StagedProducts/Elevation/1m/Projects/'):
            raise ValueError('USGS project index lacks an exact supported public object prefix')
        prefix=prefixes[0].rstrip('/')+'/'
        objects=list_objects(prefix,transport,snapshots)
        rasters=sorted(key for key in objects if key.lower().endswith('.tif'))
        if not rasters: raise ValueError('USGS indexed project has no one-meter TIFF inventory')
        for key in rasters:
            transport.check()
            stem=PurePosixPath(key).stem
            xml=[k for k in objects if PurePosixPath(k).name in (stem+'.xml',stem+'_meta.xml')]
            if len(xml)!=1: raise ValueError('USGS raster lacks its unique companion tile metadata')
            metadata_count+=1
            if metadata_count>2048: raise ValueError('USGS discovery exceeds the 2048 tile-metadata limit; partition the AOI')
            spec=objects[xml[0]]
            entry=transport.download(Asset(id=xml[0],url=BUCKET+xml[0],filename=PurePosixPath(xml[0]).name,role='metadata',**spec))
            snapshots.append({'receipt':entry.model_dump(mode='json'),'request':{'url':BUCKET+xml[0]}})
            description=project_metadata(transport.store.verify_blob(entry.sha256),stem,boxes)
            if description is None:continue
            description.update({'metadata_sha256':entry.sha256,'project_index_record':project})
            frozen=transport.inspect(Asset(id=description['publication_date']+':'+key,url=BUCKET+key,filename=PurePosixPath(key).name,metadata=description,**objects[key]))
            if frozen.etag!=objects[key]['etag'] or frozen.size!=objects[key]['size']: raise ValueError('USGS tile changed after project listing')
            assets.append(frozen)
    findings=[Finding(id='usgs-project-snapshot',rule='provider-snapshot',severity='acknowledgement',basis=BASIS,
        message='The project index and S3 listing do not provide one atomic historical snapshot. ZEUS reconciles project IDs and preserves every listing and tile metadata response; overlapping projects use publication date then object identity, with the last valid tile taking precedence.',
        evidence={'projects':len(projects),'metadata_objects':metadata_count}),
        Finding(id='usgs-datum-realization',rule='horizontal-reference',severity='acknowledgement',basis=BASIS,
        message='The resolved CRS and original provider declarations are retained per tile. Any unspecified datum realization remains unknown; ZEUS performs no vertical conversion.')]
    return assets,snapshots,findings
