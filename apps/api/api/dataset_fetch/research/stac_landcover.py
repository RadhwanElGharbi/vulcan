"""Strict annual classification discovery with preserved STAC and native grids."""
import math
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from shapely.geometry import shape

from .contracts import canonical


def check_legend(raw,product):
    actual={}
    if not isinstance(raw,list):raise ValueError('Land-cover legend is missing')
    for entry in raw:
        values=entry.get('values',[])
        if len(values)!=1 or type(values[0]) is not int or str(values[0]) in actual:
            raise ValueError('Land-cover legend has ambiguous class codes')
        actual[str(values[0])]=entry.get('summary')
    if actual!={'0':'No Data',**product.semantics['classes']}:
        raise ValueError('Land-cover legend contradicts the selected model release')


def utc(value):
    if not isinstance(value,str):raise ValueError('Annual item lacks explicit temporal bounds')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Annual bounds lack timezone')
    return result.astimezone(timezone.utc)


def discover(product,params,aoi,transport):
    from .discovery import asset
    collection=product.semantics['collection']
    definition,record=transport.json(product.endpoint+'/collections/'+collection)
    snapshots=[record]
    if definition.get('id')!=collection or definition.get('type')!='Collection' or definition.get('license')!='CC-BY-4.0':
        raise ValueError('Land-cover collection identity or licence changed')
    check_legend(definition.get('item_assets',{}).get('data',{}).get('file:values'),product)
    year=int(params['year'])
    start=datetime(year,1,1,tzinfo=timezone.utc);end=datetime(year+1,1,1,tzinfo=timezone.utc)
    body={'collections':[collection],'intersects':aoi,'datetime':f'{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z','limit':100}
    url=product.endpoint+'/search';method='POST'
    visited=set();seen=set();selected=[];expected=None
    while url:
        marker=(url,method,canonical(body))
        if marker in visited or len(visited)>=128:raise ValueError('Land-cover STAC pagination is looping or exceeds the page limit')
        visited.add(marker)
        if urlsplit(url).netloc!=urlsplit(product.endpoint).netloc or not url.startswith(product.endpoint+'/'):
            raise ValueError('Land-cover pagination changed the catalogue authority')
        value,record=transport.json(url,method=method,json_body=body if method=='POST' else None)
        snapshots.append(record)
        items=value.get('features')
        if value.get('type')!='FeatureCollection' or not isinstance(items,list):raise ValueError('Invalid annual STAC inventory')
        if value.get('numberReturned',len(items))!=len(items):raise ValueError('STAC returned count contradicts the page')
        count=value.get('numberMatched')
        if count is not None:
            if type(count) is not int or count<0 or (expected is not None and expected!=count):raise ValueError('STAC inventory count changed')
            expected=count
        for item in items:
            key=item.get('id')
            if not isinstance(key,str) or not key or key in seen:raise ValueError('Missing or duplicate annual STAC identity')
            seen.add(key)
            if len(seen)>1024:raise ValueError('Annual classification inventory exceeds 1024 items')
            if item.get('collection')!=collection:raise ValueError('STAC returned another model release')
            props=item.get('properties',{})
            first,last=utc(props.get('start_datetime')),utc(props.get('end_datetime'))
            if last<=first:raise ValueError('Annual classification time bounds are invalid')
            # STAC temporal intersection can include the previous year ending
            # exactly on January 1. It is not the requested annual product.
            if last<=start or first>=end:continue
            if first!=start or last!=end:raise ValueError('Classification does not cover exactly the selected calendar year')
            footprint=shape(item.get('geometry'))
            if footprint.is_empty or not footprint.is_valid:raise ValueError('Annual item footprint is uninterpretable')
            if not footprint.intersects(shape(aoi)):raise ValueError('Annual catalogue returned an unrelated footprint')
            raw=item.get('assets',{}).get('data',{})
            check_legend(raw.get('file:values'),product)
            href=raw.get('href','')
            if not href.startswith(product.semantics['asset_prefix']) or urlsplit(href).query or not href.endswith(f'_{year}0101-{year+1}0101.tif'):
                raise ValueError('Annual asset identity contradicts the model release or year')
            if raw.get('raster:bands')!=[{'nodata':0,'spatial_resolution':10}]:raise ValueError('Annual band contract changed')
            affine=props.get('proj:transform',[]);dimensions=props.get('proj:shape',[]);epsg=props.get('proj:epsg')
            if len(affine) not in (6,9) or not all(type(v) in (int,float) and math.isfinite(v) for v in affine) or affine[:2]!=[10,0] or affine[3:5]!=[0,-10] or (len(affine)==9 and affine[6:]!=[0,0,1]):
                raise ValueError('Annual asset lacks the expected unrotated 10 m grid')
            if len(dimensions)!=2 or any(type(v) is not int or v<=0 for v in dimensions) or type(epsg) is not int or not (32601<=epsg<=32660 or 32701<=epsg<=32760):
                raise ValueError('Annual grid dimensions or UTM CRS are invalid')
            selected.append(asset(href,id=key,metadata={'stac_item':item,'source_crs':f'EPSG:{epsg}',
                'annual_period':{'start':first.isoformat(),'end_exclusive':last.isoformat()},
                'expected_native_grid':{'width':dimensions[1],'height':dimensions[0],'x_spacing':10,'y_spacing':10,
                    'origin':[affine[2],affine[5]],'origin_tolerance':1e-7,'datatype':'Byte','bands':1,'nodata':0,'scale':1,'offset':0}}))
        links=[link for link in value.get('links',[]) if link.get('rel')=='next']
        if len(links)>1:raise ValueError('Ambiguous annual STAC pagination')
        if not links:break
        link=links[0];url=urljoin(url,link['href']);method=link.get('method','GET').upper()
        if method not in ('GET','POST'):raise ValueError('Unsupported STAC pagination method')
        body=({**body,**link.get('body',{})} if link.get('merge') else link.get('body',body)) if method=='POST' else {}
    if expected is not None and len(seen)!=expected:raise ValueError('Incomplete annual STAC inventory')
    if not selected:raise ValueError('No exact annual classification assets intersect the AOI')
    assets=[]
    for entry in sorted(selected,key=lambda a:a.id):
        reported=entry.metadata['stac_item']['assets']['data'].get('file:size')
        entry=transport.inspect(entry,actual_url=transport.signed_url(entry.url))
        if type(reported) is not int or reported<=0 or entry.size!=reported:raise ValueError('Annual source length contradicts the retained STAC inventory')
        assets.append(entry)
    product.observation_period={'reference_year':year,'start':start.isoformat(),'end_exclusive':end.isoformat(),
        'individual_observation_dates':'unknown; annual classification composite','basis':product.endpoint+'/collections/'+collection}
    return assets,snapshots,[]
