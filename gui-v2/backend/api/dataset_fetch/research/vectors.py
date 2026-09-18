"""Bounded-memory vector extraction and canonical ordering on disk."""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .contracts import canonical,file_hash,digest,scientific_recipe
from .national import archive_sources
from .transport import Cancelled
from .acquire import osm_features
from .validation import validate_vector


def process_vector(selection,receipts,inputs,plan,store,workdir,cancelled):
    from osgeo import ogr,osr
    from pyproj import CRS,Transformer
    from shapely.geometry import shape,mapping
    from shapely.ops import transform
    from shapely import normalize
    from .projection import auxiliary_transformer
    product=selection.product
    inventories=[]
    measure_records=[]
    def related_records(dataset):
        spec=product.semantics.get('auxiliary_join')
        if not spec: return {}
        layer=dataset.GetLayerByName(spec['table'])
        if layer is None or layer.GetLayerDefn().GetGeomFieldCount()!=0:
            raise ValueError('The declared supporting attribute table is missing or incompatible')
        count=layer.GetFeatureCount()
        if count<0 or count>1_000_000: raise ValueError('Supporting attribute inventory is unquantifiable or oversized')
        rows={}; seen=0; size=0
        for feature in layer:
            if cancelled(): raise Cancelled('Cancelled during supporting attribute validation')
            values=feature.items(); key=values.get(spec['key'])
            if key is None: raise ValueError('Supporting attribute table lacks its mandatory relationship key')
            size+=len(canonical(values)); seen+=1
            if size>64*1024**2: raise ValueError('Supporting attribute table exceeds the bounded join budget')
            rows.setdefault(str(key),[]).append(values)
        if seen!=count: raise ValueError('Supporting attribute table is incomplete')
        for records in rows.values(): records.sort(key=canonical)
        return rows
    def source_features():
        ids={a.id for a in selection.assets}
        if product.adapter in ('arcgis','overpass','ogc_features'):
            if product.adapter=='overpass':
                roots=next(d['overpass']['expected_roots'] for d in selection.discovery if 'overpass' in d)
                if sum(r.asset_id=='overpass-response:'+selection.id for r in receipts)!=int(bool(roots)):
                    raise ValueError('OSM frozen root inventory lacks its unique acquisition receipt')
            seen_ogc = set()
            for receipt in sorted(receipts,key=lambda r:r.asset_id):
                if receipt.asset_id.startswith('documentation:'):
                    continue
                value=json.loads(store.verify_blob(receipt.sha256).read_text(encoding='utf-8'))
                if product.adapter=='overpass' and receipt.asset_id=='overpass-response:'+selection.id:
                    roots=next(d['overpass']['expected_roots'] for d in selection.discovery if 'overpass' in d)
                    yield from osm_features(value,roots)
                elif (product.adapter=='ogc_features' and receipt.asset_id.startswith('ogc-page:')) or receipt.asset_id in ids:
                    if value.get('type')!='FeatureCollection':
                        raise ValueError('Source response is not a FeatureCollection')
                    for feature in value['features']:
                        if product.adapter == 'ogc_features':
                            key = str(feature['id'])
                            if key in seen_ogc:
                                continue  # Identical features across split AOI queries were reconciled during acquisition.
                            seen_ogc.add(key)
                        yield feature
            return
        for asset,path in inputs:
            for source in archive_sources(Path(path),product.adapter,cancelled) if path.endswith('.zip') else [path]:
                dataset=ogr.Open(source)
                if dataset is None:
                    raise ValueError('Vector source cannot be read')
                related=related_records(dataset)
                selected_layer=None
                if product.semantics.get('layer_by_status'):
                    selected_layer=product.semantics['layer_by_status'][selection.parameters['status']]
                    if dataset.GetLayerByName(selected_layer) is None:
                        raise ValueError('The archive lacks the exact selected spatial layer')
                for layer in dataset:
                    if layer.GetLayerDefn().GetGeomFieldCount()==0 or (selected_layer and layer.GetName()!=selected_layer):
                        inventories.append({'asset':asset.id,'layer':layer.GetName(),'source_features':layer.GetFeatureCount(),
                                            'disposition':'retained supporting table' if layer.GetLayerDefn().GetGeomFieldCount()==0 else 'outside selected status; exact original layer retained'})
                        continue
                    reference=layer.GetSpatialRef()
                    if reference is None:
                        raise ValueError('Source vector CRS is missing')
                    crs=CRS.from_wkt(reference.ExportToWkt())
                    source_count=layer.GetFeatureCount()
                    project=auxiliary_transformer(selection.recipe,4326,crs,plan.aoi)
                    bounds=project.transform_bounds(*shape(plan.aoi).bounds,densify_pts=101)
                    layer.SetSpatialFilterRect(*bounds)
                    candidate_count=layer.GetFeatureCount()
                    inventories.append({'asset':asset.id,'layer':layer.GetName(),'source_features':source_count,'bounding_box_candidates':candidate_count,'filter_crs':crs.to_string(),'filter_bounds':bounds})
                    to_wgs=auxiliary_transformer(selection.recipe,crs,4326,plan.aoi).transform
                    returned=0
                    for feature in layer:
                        if cancelled(): raise Cancelled('Cancelled while reading source vectors')
                        raw=feature.GetGeometryRef()
                        if raw is None: raise ValueError('Source contains null geometry')
                        props=feature.items()
                        if raw.HasCurveGeometry():
                            raise ValueError('Curved source geometry requires an explicit value-preserving conversion; implicit linearization is disabled')
                        if raw.IsMeasured():
                            native=bytes(raw.ExportToIsoWkb())
                            if len(native)>16*1024**2: raise ValueError('Native measured geometry exceeds the per-feature preservation budget')
                            if 'zeus_native_measured_geometry' in props: raise ValueError('Source uses a reserved geometry provenance attribute')
                            props['zeus_native_measured_geometry']={'encoding':'ISO WKB hex','crs':crs.to_wkt(),'wkb':native.hex(),
                                'scope':'Unclipped original source geometry; all M values retained without interpretation or interpolation. Project geometry is a separately clipped XY/Z derivative.',
                                'm_units':'unknown; optional opaque source measure, not an analytical quantity qualified by this adapter'}
                            measure_records.append(f'{asset.id}:{layer.GetName()}:{feature.GetFID()}')
                            raw=raw.Clone();raw.SetMeasured(False)
                        geometry=transform(to_wgs,shape(json.loads(raw.ExportToJson())))
                        if product.semantics.get('auxiliary_join'):
                            key=props.get(product.semantics['auxiliary_join']['key'])
                            if key is None: raise ValueError('Spatial feature lacks its mandatory supporting-table relationship key')
                            props['zeus_related_source_records']=related.get(str(key),[])
                        props['source_id']=f'{asset.id}:{layer.GetName()}:{feature.GetFID()}'
                        returned+=1
                        yield {'type':'Feature','properties':props,'geometry':mapping(geometry)}
                    if returned!=candidate_count:
                        raise ValueError('Vector layer returned fewer features than its filtered inventory')
    database=workdir/'canonical-vectors.sqlite'
    database.unlink(missing_ok=True)
    aoi=shape(plan.aoi)
    project=auxiliary_transformer(selection.recipe,4326,plan.target_crs,plan.aoi).transform
    target_aoi=transform(project,aoi)
    input_count,skipped,count=0,0,0
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('PRAGMA temp_store=FILE')
        connection.execute('CREATE TABLE records (id TEXT PRIMARY KEY, geometry BLOB, attributes TEXT)')
        for feature in source_features():
            if cancelled(): raise Cancelled('Cancelled during vector processing')
            input_count+=1
            if input_count>1_000_000: raise ValueError('Vector AOI exceeds the one-million feature limit')
            props=feature.get('properties') or {}
            key=props.get('source_id',feature.get('id'))
            if key is None: raise ValueError('Mandatory source feature identity is missing')
            key=str(key)
            geom=shape(feature['geometry'])
            if geom.is_empty or not geom.is_valid: raise ValueError('Invalid source geometry; automatic repair is not enabled')
            clipped=geom.intersection(aoi)
            projected=None
            if clipped.is_empty: skipped+=1
            else:
                projected=normalize(transform(project,clipped).intersection(target_aoi))
                if projected.is_empty or not projected.is_valid: raise ValueError('Clipping or projection produced invalid geometry')
                count+=1
            try: connection.execute('INSERT INTO records VALUES(?,?,?)',(key,projected.wkb if projected is not None else None,canonical({**props,'source_id':key}).decode()))
            except sqlite3.IntegrityError as exc: raise ValueError('Duplicate source feature identity') from exc
        output=workdir/f'{selection.id}.gpkg'
        output.unlink(missing_ok=True)
        dataset=ogr.GetDriverByName('GPKG').CreateDataSource(str(output))
        reference=osr.SpatialReference(); reference.SetFromUserInput(plan.target_crs); reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        layer=dataset.CreateLayer(product.category,reference,ogr.wkbUnknown,options=['SPATIAL_INDEX=YES'])
        layer.CreateField(ogr.FieldDefn('source_id',ogr.OFTString)); layer.CreateField(ogr.FieldDefn('source_properties',ogr.OFTString))
        dataset.StartTransaction()
        for key,geometry,props in connection.execute('SELECT * FROM records WHERE geometry IS NOT NULL ORDER BY id COLLATE BINARY'):
            if cancelled(): raise Cancelled('Cancelled during canonical vector output')
            feature=ogr.Feature(layer.GetLayerDefn()); feature.SetField('source_id',key); feature.SetField('source_properties',props); feature.SetGeometry(ogr.CreateGeometryFromWkb(geometry))
            if layer.CreateFeature(feature)!=0: raise ValueError('GeoPackage feature write failed')
        if dataset.CommitTransaction()!=0: raise ValueError('GeoPackage transaction failed')
        layer=None; dataset=None
    result,issues=validate_vector(output,plan.aoi,selection_id=selection.id,expected_count=count,cancelled=cancelled,recipe=selection.recipe)
    if measure_records:
        from .contracts import Finding
        issues.append(Finding(id=selection.id+':native-measures',rule='optional-native-measures',severity='acknowledgement',
            message='Optional source M values are retained with their native CRS in the zeus_native_measured_geometry attribute. Their units are unknown, so ZEUS does not qualify them as an analytical quantity. Clipped project geometry uses XY/Z only; no M interpolation was performed.',
            evidence={'source_features_with_measures':len(measure_records),'feature_ids':sorted(measure_records)}))
    result['scientific_hash']=digest({'vector':result['scientific_hash'],'recipe':scientific_recipe(selection.recipe),'parameters':selection.parameters,'product_semantics':product.semantics})
    result.update({'selection_id':selection.id,'artifact':output.name,'input_features':input_count,'outside_aoi_features':skipped,'source_inventories':inventories,'repair_policy':'reject-invalid','unexplained_losses':0,
                   'clipping':'source geometry intersected with geographic AOI, then constrained to the transformed AOI after projection','processing_recipe':selection.recipe,'native_measured_features':len(measure_records)})
    database.unlink(missing_ok=True)
    return [{'id':output.stem,'name':product.name,'category':product.category,'kind':'vector','file':output.name,'sha256':file_hash(output),'scientific_hash':result['scientific_hash'],'product':product.model_dump(mode='json'),'parameters':selection.parameters,'crs':plan.target_crs,'recipe':selection.recipe}], [result],issues
