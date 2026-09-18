from __future__ import annotations
import hashlib
import json
import sqlite3
from contextlib import closing
import tempfile
from pathlib import Path
from .contracts import canonical,digest
from .transport import Cancelled


def validate_vector(path,aoi,*,selection_id,expected_count=None,cancelled=lambda:False,legacy_inventory=False,recipe=None):
    from osgeo import ogr
    from pyproj import CRS,Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    from shapely import normalize
    dataset=ogr.Open(str(path))
    if dataset is None: raise ValueError('Vector dataset is unreadable')
    result=[]
    science=hashlib.sha256(canonical({'aoi':digest(aoi),'ordering':'layer then source_id'}))
    with tempfile.TemporaryDirectory(prefix='validate-',dir=path.parent) as temporary:
        with closing(sqlite3.connect(Path(temporary)/'records.sqlite')) as connection:
            connection.execute('PRAGMA temp_store=FILE')
            connection.execute('CREATE TABLE records (id TEXT PRIMARY KEY, payload BLOB)')
            for layer in dataset:
                reference=layer.GetSpatialRef()
                if reference is None: raise ValueError('Vector CRS is missing')
                crs=CRS.from_wkt(reference.ExportToWkt())
                from .projection import auxiliary_transformer
                aoi_geometry=transform(auxiliary_transformer(recipe,4326,crs,aoi).transform,shape(aoi))
                count=0;outside=0
                connection.execute('DELETE FROM records')
                for feature in layer:
                    if cancelled(): raise Cancelled('Cancelled during vector validation')
                    raw=feature.GetGeometryRef()
                    if raw is None or raw.IsEmpty() or not raw.IsValid(): raise ValueError('Vector contains missing, empty or invalid geometry')
                    geometry=shape(json.loads(raw.ExportToJson()))
                    if not aoi_geometry.buffer(1e-8).covers(geometry):
                        outside+=1
                        if not legacy_inventory: raise ValueError('Processed geometry extends beyond AOI clipping tolerance (1e-8 CRS units)')
                    fields={feature.GetFieldDefnRef(i).GetName():feature.GetField(i) for i in range(feature.GetFieldCount())}
                    key=str(feature.GetFID()) if legacy_inventory else fields.get('source_id')
                    if key is None: raise ValueError('Processed vector source identity is missing')
                    payload=canonical({'geometry':normalize(geometry).wkb_hex,'properties':fields})
                    try: connection.execute('INSERT INTO records VALUES(?,?)',(key,payload))
                    except sqlite3.IntegrityError as exc: raise ValueError('Duplicate source feature identity') from exc
                    count+=1
                science.update(canonical({'schema':[(field.GetName(),field.GetTypeName()) for field in layer.schema],'crs':crs.to_wkt(),'layer':layer.GetName(),'feature_count':count}))
                for row in connection.execute('SELECT payload FROM records ORDER BY id COLLATE BINARY'):
                    science.update(len(row[0]).to_bytes(8,'little')); science.update(row[0])
                result.append({'layer':layer.GetName(),'feature_count':count,'crs':crs.to_string(),'status':'verified_empty' if count==0 else 'passed','features_extending_outside_aoi':outside,
                               'identity_basis':'present artifact layer/FID; publisher identity unknown' if legacy_inventory else 'retained source identity'})
    count=sum(item['feature_count'] for item in result)
    if expected_count is not None and count!=expected_count: raise ValueError('Unexpected feature loss during processing')
    rules=[{'rule':rule,'status':'passed','basis':'ZEUS policy: complete-extract/1.0'} for rule in ('interpretable-crs','unique-source-identities','valid-geometries','aoi-clipping','feature-count-reconciliation','complete-scan')]
    for rule in rules:
        if (legacy_inventory and rule['rule'] in ('unique-source-identities','aoi-clipping')) or (expected_count is None and rule['rule']=='feature-count-reconciliation'):
            rule.update({'status':'not_assessed','reason':'Legacy/current artifact inventory cannot establish this historical acquisition property' if legacy_inventory else 'No expected count supplied'})
    return {'kind':'vector','layers':result,'feature_count':count,'scientific_hash':science.hexdigest(),
            'rule_results':rules,
            'fitness_for_analysis':'not assessed'},[]
