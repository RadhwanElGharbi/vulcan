"""Resolve coordinate operations before confirmation, then execute their pipelines."""
from __future__ import annotations
import warnings
from .contracts import Finding,digest

BASIS='https://pyproj4.github.io/pyproj/stable/api/transformer.html#pyproj.transformer.TransformerGroup'


def resolve_operation(source,target,aoi,*,always_xy=False):
    from pyproj import CRS
    from pyproj.transformer import TransformerGroup
    from pyproj.aoi import AreaOfInterest
    from shapely.geometry import shape
    source,target=CRS.from_user_input(source).to_2d(),CRS.from_user_input(target).to_2d()
    bounds=shape(aoi).bounds if aoi is not None else None
    with warnings.catch_warnings():
        # This exact warning becomes structured review evidence below.
        warnings.filterwarnings('ignore',message='Best transformation is not available due to missing Grid',category=UserWarning)
        group=TransformerGroup(source,target,area_of_interest=AreaOfInterest(*bounds) if bounds else None,allow_ballpark=False,always_xy=always_xy)
    if not group.transformers:
        raise ValueError('No installed non-ballpark coordinate operation exists for the selected CRS pair')
    selected=group.transformers[0]
    region=selected.area_of_use
    if region and bounds and not (region.south<=bounds[1] and region.north>=bounds[3] and region.west<=bounds[0] and region.east>=bounds[2]):
        raise ValueError('No single selected coordinate operation covers the complete AOI; partition the project')
    record={'source_crs':source.to_wkt(),'target_crs':target.to_wkt(),'pipeline':selected.definition,
            'name':selected.description,'stated_accuracy_m':selected.accuracy if selected.accuracy>=0 else None,
            'accuracy_scope':'Coordinate operation only; not source measurement or local AOI accuracy',
            'area_of_use':{'name':region.name,'bounds':list(region.bounds)} if region else None,
            'best_known_operation_available':group.best_available,
            'missing_grids':sorted({g.short_name for op in group.unavailable_operations for g in op.grids if not g.available}),
            'selection_policy':'First non-ballpark operation in the pinned PROJ database ordering for the complete AOI; no runtime reselection',
            'coordinate_order':'traditional GIS x/y' if always_xy else 'CRS axis order','basis':BASIS}
    return record


def native_systems(product,assets):
    from pyproj import CRS
    systems={}
    for asset in assets or [None]:
        metadata=asset.metadata if asset else {}
        epsg=metadata.get('asset',{}).get('proj:epsg') or metadata.get('properties',{}).get('proj:epsg')
        source=metadata.get('source_crs') or product.semantics.get('source_crs') or (f'EPSG:{epsg}' if epsg else None)
        if product.adapter in ('arcgis','ogc_features','overpass','cds'): source='EPSG:4326'
        if product.kind=='vector' and not source: source=product.semantics.get('crs')
        if not source: raise ValueError('Discovery must establish the native CRS before confirmation: '+product.id)
        crs=CRS.from_user_input(source).to_2d()
        systems[crs.to_string()]=crs
    return [value for _,value in sorted(systems.items())]


def freeze_auxiliary_operations(product,assets,recipe,aoi):
    from pyproj import CRS
    systems=native_systems(product,assets) if recipe['native_export'] or product.kind=='vector' else []
    if not recipe['native_export']: systems.append(CRS.from_user_input(recipe['target_crs']))
    pairs={}
    for crs in systems:
        for source,target in ((CRS.from_epsg(4326),crs),(crs,CRS.from_epsg(4326))):
            pairs[(source.to_string(),target.to_string())]=(source,target)
    operations=[resolve_operation(source,target,aoi,always_xy=True) for _,(source,target) in sorted(pairs.items())]
    recipe['auxiliary_operations']=operations
    return [Finding(id='auxiliary-coordinate-operation:'+digest(op)[:16],rule='coordinate-operation-accuracy',severity='acknowledgement',basis=BASIS,
        message=f"AOI filtering, clipping or validation uses {op['name']}, with stated coordinate-operation accuracy {op['stated_accuracy_m'] if op['stated_accuracy_m'] is not None else 'unknown'} m. Missing higher-accuracy grids: {', '.join(op['missing_grids']) or 'none'}.",evidence=op)
        for op in operations if not op['best_known_operation_available'] or op['stated_accuracy_m'] is None]


def auxiliary_transformer(recipe,source,target,aoi):
    from pyproj import CRS,Transformer
    source,target=CRS.from_user_input(source).to_2d(),CRS.from_user_input(target).to_2d()
    if recipe is None or 'auxiliary_operations' not in recipe:
        # Standalone artifact validation and hand-built fixtures have no plan.
        # Every production plan carries this key, including an empty inventory.
        operation=resolve_operation(source,target,aoi,always_xy=True)
    else:
        matches=[op for op in recipe['auxiliary_operations'] if source.equals(CRS.from_wkt(op['source_crs'])) and target.equals(CRS.from_wkt(op['target_crs']))]
        if len(matches)!=1: raise ValueError('Actual CRS has no unique auxiliary coordinate operation in the confirmed plan')
        operation=matches[0]
    return Transformer.from_pipeline(operation['pipeline'])


def freeze_raster_operations(product,assets,recipe,aoi):
    if product.kind!='raster' or recipe['native_export']:
        return []
    from pyproj import CRS
    systems={}
    for asset in assets:
        metadata=asset.metadata
        epsg=metadata.get('asset',{}).get('proj:epsg') or metadata.get('properties',{}).get('proj:epsg')
        source=metadata.get('source_crs') or product.semantics.get('source_crs') or (f'EPSG:{epsg}' if epsg else None)
        if not source:
            raise ValueError('Discovery must establish the native CRS before an analytical transformation can be confirmed: '+asset.id)
        crs=CRS.from_user_input(source).to_2d()
        if not any(crs.equals(previous) for previous in systems.values()):
            systems[crs.to_string()]=crs
    operations=[resolve_operation(crs,recipe['target_crs'],aoi) for _,crs in sorted(systems.items())]
    recipe['coordinate_operations']=operations
    recipe['mosaic_policy']='Frozen asset order; one resampling per input onto the exact output grid; last valid asset wins; merge without resampling'
    recipe['clipping_order']='After resampling: preserve cells with positive AOI intersection, including boundary cells; set every other cell to NoData'
    recipe['output_datatype']='Byte' if product.category=='landcover' else 'Float32'
    recipe['vertical_operation']='none; source heights retained'
    findings=[]
    for operation in operations:
        if not operation['best_known_operation_available'] or operation['stated_accuracy_m'] is None:
            findings.append(Finding(id='coordinate-operation:'+digest(operation)[:16],rule='coordinate-operation-accuracy',severity='acknowledgement',
                message=f"Coordinate conversion uses {operation['name']}, with stated operation accuracy {operation['stated_accuracy_m'] if operation['stated_accuracy_m'] is not None else 'unknown'} m. Missing optional higher-accuracy grids: {', '.join(operation['missing_grids']) or 'none'}. This does not establish local dataset accuracy.",basis=BASIS,evidence=operation))
    return findings


def frozen_operation(recipe,source):
    from pyproj import CRS
    source=CRS.from_user_input(source).to_2d()
    matches=[operation for operation in recipe.get('coordinate_operations',[]) if source.equals(CRS.from_wkt(operation['source_crs']))]
    if len(matches)!=1:
        raise ValueError('Actual source CRS has no unique coordinate operation in the confirmed plan')
    return matches[0]


def mask_after_resampling(dataset,aoi,cancelled,recipe=None):
    import numpy as np
    import shapely
    from shapely.geometry import shape
    from shapely.ops import transform
    from pyproj import Transformer,CRS
    from .transport import Cancelled
    crs=CRS.from_wkt(dataset.GetProjection())
    geom=transform(auxiliary_transformer(recipe,4326,crs,aoi).transform,shape(aoi))
    gt=dataset.GetGeoTransform()
    for y in range(0,dataset.RasterYSize,256):
        for x in range(0,dataset.RasterXSize,256):
            if cancelled(): raise Cancelled('Cancelled during final AOI clipping')
            h,w=min(256,dataset.RasterYSize-y),min(256,dataset.RasterXSize-x)
            yy,xx=np.mgrid[y:y+h,x:x+w]
            corners=np.stack([np.stack([gt[0]+(xx+dx)*gt[1]+(yy+dy)*gt[2],gt[3]+(xx+dx)*gt[4]+(yy+dy)*gt[5]],axis=-1) for dx,dy in [(0,0),(1,0),(1,1),(0,1),(0,0)]],axis=-2)
            cells=shapely.polygons(corners)
            inside=shapely.contains(geom,cells)
            boundary=shapely.intersects(geom,cells)&~inside
            inside[boundary]=shapely.area(shapely.intersection(cells[boundary],geom))>0
            if np.all(inside): continue
            for i in range(1,dataset.RasterCount+1):
                band=dataset.GetRasterBand(i);nodata=band.GetNoDataValue()
                if nodata is None: raise ValueError('Analytical clipping requires an explicit NoData value')
                values=band.ReadAsArray(x,y,w,h)
                if values is None: raise ValueError('Cannot read complete raster block during clipping')
                values[~inside]=nodata
                if band.WriteArray(values,x,y)!=0: raise ValueError('Could not persist the AOI clipping mask')
