"""Read-only provider choices for the UI; acquisition still freezes its own inventory."""
from datetime import date, datetime, timezone
from functools import lru_cache
import calendar
import json
import time
from urllib.parse import urlparse
import requests

from .registry import registry

@lru_cache(maxsize=24)
def _cached_json(url, body, bucket):
    with requests.request('POST' if body else 'GET',url,json=json.loads(body) if body else None,timeout=(5,25),stream=True) as response:
        response.raise_for_status()
        chunks=[]; size=0
        for chunk in response.iter_content(65536):
            size+=len(chunk)
            if size>8_000_000: raise ValueError('Provider availability response exceeds the browser catalogue limit')
            chunks.append(chunk)
        return json.loads(b''.join(chunks))

def _json(url, body=None):
    return _cached_json(url,json.dumps(body,sort_keys=True) if body else '',int(time.time()//300))

def climate_dates(rows, year, variables):
    hours={variable:{} for variable in variables}
    for row in rows:
        if 'reanalysis' not in row.get('product_type',[]) or str(year) not in row.get('year',[]): continue
        for variable in set(variables)&set(row.get('variable',[])):
            for month in row.get('month',[]):
                for day in row.get('day',[]):
                    try: stamp=date(year,int(month),int(day)).isoformat()
                    except ValueError: continue
                    hours[variable].setdefault(stamp,set()).update(row.get('time',[]))
    full={f'{hour:02d}:00' for hour in range(24)}
    available=[{stamp for stamp,values in dates.items() if full<=values} for dates in hours.values()]
    return sorted(set.intersection(*available)) if available else []

def availability(product_id, project=None, year=None, month=None, country=None, variables=None):
    product=registry()[product_id]
    result={'product_id':product_id,'years':[],'dates':[],'scenes':[],'checked_at':datetime.now(timezone.utc).isoformat(),'basis':'Registered product releases; asset availability verified during discovery'}
    if product.adapter=='worldpop':
        if not country and project:
            from ..utils import _load_project_context
            countries=_load_project_context(project).iso3_list
            if len(countries)==1: country=countries[0]
        if not country or len(country)!=3 or not country.isalpha(): raise ValueError('Choose a three-letter country before listing population releases')
        url=product.endpoint+'?iso3='+country.lower()
        data=_json(url)
        rows=data.get('data')
        if not isinstance(rows,list): raise ValueError('WorldPop returned no interpretable release catalogue')
        spec=product.parameters['year']
        result.update(years=sorted({int(row['popyear']) for row in rows if str(row.get('popyear','')).isdigit() and spec['min']<=int(row['popyear'])<=spec['max'] and any(str(f).lower().endswith('.tif') for f in row.get('files',[]))}),country=country.upper(),basis=url)
    elif product.adapter=='cds':
        url=product.endpoint+'/catalogue/v1/collections/'+product.semantics['dataset']
        collection=_json(url)
        link=next(x['href'] for x in collection['links'] if x['rel']=='constraints')
        if urlparse(link).hostname!='object-store.os-api.cci2.ecmwf.int': raise ValueError('Unrecognized CDS constraints host')
        rows=_json(link)
        variables=variables or product.parameters['variables']['default']
        if not set(variables)<=set(product.parameters['variables']['values']): raise ValueError('Unsupported climate variable')
        years=sorted({int(y) for row in rows if 'reanalysis' in row.get('product_type',[]) and set(variables)&set(row.get('variable',[])) for y in row.get('year',[])})
        dates=climate_dates(rows,year,variables) if year in years else []
        dates=[d for d in dates if d<date.today().isoformat()]
        result.update(years=years,dates=dates,basis=link,note='Dates with all 24 hourly times listed for every selected variable. Preliminary/final status is checked in the fetched response.')
    elif product.adapter=='stac':
        url=product.endpoint+'/collections/'+product.semantics['collection']
        collection=_json(url)
        intervals=collection['extent']['temporal']['interval']
        years=sorted({y for start,end in intervals for y in range(int(start[:4]),int((end or date.today().isoformat())[:4])+1)})
        result.update(years=years,basis=url,note='Browse a catalogue year to list actual acquisitions intersecting this project. Empty years have no matching scenes.')
        if year is not None:
            if year not in years: raise ValueError('Year is outside the provider catalogue')
            if not project: raise ValueError('Select a project to list imagery acquisitions')
            from ..utils import _load_project_context
            from .aoi import read_aoi
            aoi,_=read_aoi(_load_project_context(project).cutline_path)
            start=date(year,month or 1,1); end=date(year,month or 12,calendar.monthrange(year,month)[1] if month else 31)
            body={'collections':[product.semantics['collection']],'intersects':aoi,'datetime':f'{start}T00:00:00Z/{end}T23:59:59Z','limit':100}
            target=product.endpoint+'/search'; seen=set(); scenes={}
            while target:
                signature=json.dumps([target,body],sort_keys=True)
                if signature in seen: raise ValueError('Provider repeated an availability page')
                seen.add(signature)
                if len(seen)>20: raise ValueError('Too many scenes for one year. Choose a month to list a complete result.')
                page=_json(target,body)
                if page.get('type')!='FeatureCollection' or not isinstance(page.get('features'),list): raise ValueError('Invalid imagery availability response')
                for item in page['features']:
                    stamp=item['properties']['datetime']; key=item['id']
                    if key in scenes: raise ValueError('Provider repeated a scene across pages')
                    scenes[key]={'id':key,'date':stamp[:10],'datetime':stamp,'cloud_cover':item['properties'].get('eo:cloud_cover')}
                following=next((x for x in page.get('links',[]) if x['rel']=='next'),None)
                target=following['href'] if following else None
                if target and (urlparse(target).scheme!='https' or urlparse(target).netloc!=urlparse(product.endpoint).netloc): raise ValueError('Unrecognized imagery pagination host')
                if following and following.get('method','GET')=='POST': body={**(body or {}),**following.get('body',{})} if following.get('merge') else following.get('body',body)
                else: body=None
            result.update(dates=sorted({s['date'] for s in scenes.values()}),scenes=sorted(scenes.values(),key=lambda s:(s['datetime'],s['id'])))
    elif 'year' in product.parameters and product.parameters['year'].get('values'):
        result['years']=product.parameters['year']['values']
    return result
