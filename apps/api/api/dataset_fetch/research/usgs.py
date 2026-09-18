"""USGS published seamless tile interface; never substitutes another DEM product."""
from __future__ import annotations
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from .contracts import Asset, Finding


def tile_metadata(path, tile):
    root=ET.parse(path).getroot()
    def value(path):
        nodes=root.findall(path)
        if len(nodes)!=1 or not nodes[0].text or not nodes[0].text.strip():
            raise ValueError('USGS tile metadata lacks a unique mandatory field: '+path)
        return nodes[0].text.strip()
    title=value('idinfo/citation/citeinfo/title')
    horizontal=value('spref/horizsys/geodetic/horizdn')
    vertical=value('spref/vertdef/altsys/altdatum')
    units=value('spref/vertdef/altsys/altunits')
    if tile not in title.lower() or units!='meters':
        raise ValueError('USGS tile metadata contradicts the selected tile identity or units')
    source_crs={'North American Datum of 1983':'EPSG:4269','World Geodetic System 1984':'EPSG:4326'}.get(horizontal)
    if not source_crs:
        raise ValueError('USGS tile metadata horizontal datum has no implemented unambiguous CRS mapping')
    spacing=[abs(float(value('spref/horizsys/geograph/'+name))) for name in ('longres','latres')]
    if value('spref/horizsys/geograph/geogunit')!='Decimal degrees' or not all(math.isfinite(v) and v>0 for v in spacing):
        raise ValueError('USGS tile metadata native spacing cannot be interpreted')
    def date(path): return datetime.strptime(value(path),'%Y%m%d').date().isoformat()
    period={'start':date('idinfo/timeperd/timeinfo/rngdates/begdate'),'end':date('idinfo/timeperd/timeinfo/rngdates/enddate'),
            'currentness':value('idinfo/timeperd/current')}
    if period['start']>period['end']:
        raise ValueError('USGS tile metadata time range is reversed')
    return {'title':title,'publication_date':date('idinfo/citation/citeinfo/pubdate'),'provider_time_range':period,
            'horizontal_datum':horizontal,'vertical_datum':vertical,'vertical_units':units,'source_crs':source_crs,
            'native_spacing':{'x':spacing[0],'y':spacing[1],'units':'degree'},
            'accuracy':{'value':'unknown','reason':'Selected tile FGDC metadata provides no quantified local positional accuracy'}}


def discover_seamless(product, boxes, transport):
    cells=set()
    for w,s,e,n in boxes:
        cells.update((lat,lon) for lat in range(math.floor(s),math.ceil(n)) for lon in range(math.floor(w),math.ceil(e)))
    if len(cells)>2048: raise ValueError('USGS selection exceeds the 2048 asset limit')
    assets,snapshots,findings=[],[],[]
    for south,west in sorted(cells):
        north=south+1
        tile=f'{"n" if north>=0 else "s"}{abs(north):02d}{"e" if west>=0 else "w"}{abs(west):03d}'
        stem=f'USGS_{product.semantics["tile_code"]}_{tile}'
        url=product.semantics['staged_prefix']+'/'+tile+'/'+stem
        try:
            entry=transport.inspect(Asset(id=stem,url=url+'.tif',filename=stem+'.tif'))
        except ValueError as exc:
            if 'HTTP 404' not in str(exc): raise
            findings.append(Finding(id=stem+':absent',rule='catalogue-coverage',severity='acknowledgement',message=f'USGS publishes no {product.name} tile {tile}; no alternate DEM will be used.',evidence={'url':url+'.tif','bbox':[west,south,west+1,north]}))
            continue
        metadata=transport.download(transport.inspect(Asset(id=stem+':metadata',url=url+'.xml',filename=stem+'.xml',role='metadata')))
        entry.metadata={**tile_metadata(transport.store.verify_blob(metadata.sha256),tile),'footprint':[west,south,west+1,north],
                        'metadata_sha256':metadata.sha256,'access_method':'USGS staged seamless tile with companion FGDC metadata'}
        snapshots.append({'receipt':metadata.model_dump(mode='json'),'request':{'url':url+'.xml'}})
        assets.append(entry)
    return assets,snapshots,findings


def describe_selection(product, assets):
    metadata={a.id:a.metadata for a in assets}
    product.version='; '.join(a.metadata['title'] for a in assets)
    releases=sorted({a.metadata['publication_date'] for a in assets})
    product.release_date=releases[0] if len(releases)==1 else None
    product.observation_period={'value':'unknown','reason':'FGDC time ranges describe provider currentness; they are not asserted as observation dates',
                                'provider_time_ranges':{key:m['provider_time_range'] for key,m in metadata.items()}}
    product.native_spacing={key:m['native_spacing'] for key,m in metadata.items()}
    product.accuracy={'value':'unknown','reason':'Tile metadata does not quantify positional accuracy within the AOI'}
    product.semantics.update({'tile_release_dates':{key:m['publication_date'] for key,m in metadata.items()},
                              'vertical_references':{key:m['vertical_datum'] for key,m in metadata.items()},
                              'vertical_processing':'No vertical datum transformation; source heights retained'})
    if len({a.metadata['vertical_datum'] for a in assets})>1:
        raise ValueError('Selected USGS tiles have different vertical references; one analytical mosaic cannot mix them')
