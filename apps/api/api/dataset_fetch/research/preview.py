"""Integrity-bound display context; native analytical arrays remain unchanged."""
import json
from pathlib import Path

from .contracts import canonical, digest, file_hash
from .readers import active_datasets

PREVIEW_POLICY='web-mercator-frozen-aoi-validity-mask/1'


def preview_context(project_path, raster_path):
    for item in active_datasets(project_path):
        if (project_path/item['path']).resolve()!=raster_path.resolve():continue
        if file_hash(raster_path)!=item['sha256']:raise ValueError('Preview source failed its integrity check')
        aoi=item.get('aoi')
        if aoi is None:
            # Earlier scientific generations retained the AOI in their plan.
            # Resolve that exact generation, never the project's current AOI.
            generation=next(p for p in raster_path.parents if p.parent.name=='generations')
            manifest=json.loads((generation/'manifest.json').read_text(encoding='utf-8'))
            retained_plan=generation/'plan.json'
            if retained_plan.exists():
                plan=json.loads(retained_plan.read_text(encoding='utf-8'))
            else:
                from .store import Store
                plan=Store().plan(manifest['plan_hash'][:32])
            # Portable generations carry their own frozen plan. A corrupt local
            # plan must fail, never fall back to a different ledger copy.
            body={k:v for k,v in plan.items() if k not in ('plan_id','plan_hash')}
            if plan['plan_hash']!=manifest['plan_hash'] or digest(body)!=manifest['plan_hash'] or plan['plan_id']!=manifest['plan_hash'][:32]:
                raise ValueError('Preview plan identity differs from its generation')
            aoi=plan['aoi']
            if digest(aoi)!=plan['aoi_hash']:raise ValueError('Preview AOI failed its retained plan integrity check')
        if item.get('aoi_hash') and digest(aoi)!=item['aoi_hash']:
            raise ValueError('Preview AOI failed its integrity check')
        gap=None
        if item.get('gap_path'):
            gap=(project_path/item['gap_path']).resolve()
            if not gap.is_relative_to(project_path.resolve()) or file_hash(gap)!=item['gap_sha256']:
                raise ValueError('Preview validity mask failed its integrity check')
        categorical=item['category']=='landcover' or item.get('source_metadata',{}).get('band')=='SCL' or item.get('product',{}).get('units') in ('class_code','mapping_unit_id')
        identity=digest({'source':item['sha256'],'aoi':aoi,'recipe':item.get('recipe'),'gap':item.get('gap_sha256'),'categorical':categorical,'preview_policy':PREVIEW_POLICY})
        return {'identity':int(identity,16),'aoi_json':canonical(aoi).decode(),'gap_mask':str(gap) if gap else None,'categorical':categorical}
    return None


def validity_alpha(path,bounds):
    from osgeo import gdal
    import numpy as np
    gdal.UseExceptions()
    source=gdal.Open(str(path))
    if source is None:raise ValueError('Cannot read the committed validity mask')
    first=gdal.Translate('',source,format='VRT',bandList=[1])
    warped=gdal.Warp('',first,format='MEM',dstSRS='EPSG:3857',outputBounds=bounds,width=256,height=256,
        resampleAlg='near',srcNodata=255,dstNodata=255,multithread=False,warpOptions=['NUM_THREADS=1'])
    if warped is None:raise ValueError('Cannot project the committed validity mask for display')
    values=warped.GetRasterBand(1).ReadAsArray()
    if values is None or values.shape!=(256,256):raise ValueError('Incomplete display validity mask')
    return np.where(values==0,255,0).astype(np.uint8)
