"""Validate the declared attribute/geometry contract of each retained ArcGIS batch."""
from __future__ import annotations
import math


def validate_features(payload,metadata):
    fields=[f for f in metadata['fields'] if f['type']!='esriFieldTypeGeometry']
    shapes={'esriGeometryPoint':{'Point'},'esriGeometryMultipoint':{'MultiPoint'},'esriGeometryPolyline':{'LineString','MultiLineString'},'esriGeometryPolygon':{'Polygon','MultiPolygon'}}
    expected=shapes.get(metadata.get('geometryType'))
    if expected is None: raise ValueError('ArcGIS geometry type has no implemented faithful conversion')
    for feature in payload['features']:
        properties=feature.get('properties')
        geometry=feature.get('geometry')
        if not isinstance(properties,dict) or not isinstance(geometry,dict) or geometry.get('type') not in expected:
            raise ValueError('ArcGIS feature violates its declared attributes or geometry type')
        subtype={}
        if metadata.get('typeIdField') and metadata.get('types'):
            values=[row for row in metadata['types'] if row['id']==properties.get(metadata['typeIdField'])]
            if len(values)!=1: raise ValueError('ArcGIS feature has an unknown subtype identifier')
            subtype=values[0].get('domains',{})
        for field in fields:
            key=field['name']
            if key not in properties: raise ValueError('ArcGIS omitted a declared attribute: '+key)
            value=properties[key]
            if value is None:
                if field.get('nullable') is False: raise ValueError('ArcGIS returned null for a mandatory attribute: '+key)
                continue
            kind=field['type']
            if kind not in ('esriFieldTypeOID','esriFieldTypeInteger','esriFieldTypeSmallInteger','esriFieldTypeBigInteger','esriFieldTypeDouble','esriFieldTypeSingle','esriFieldTypeDate','esriFieldTypeString','esriFieldTypeGUID','esriFieldTypeGlobalID','esriFieldTypeXML'):
                raise ValueError('ArcGIS field type has no implemented scientific contract: '+kind)
            if kind in ('esriFieldTypeOID','esriFieldTypeInteger','esriFieldTypeSmallInteger','esriFieldTypeBigInteger') and type(value) is not int:
                raise ValueError('ArcGIS integer attribute has the wrong type: '+key)
            if kind in ('esriFieldTypeDouble','esriFieldTypeSingle','esriFieldTypeDate') and (type(value) not in (int,float) or not math.isfinite(value)):
                raise ValueError('ArcGIS numerical/date attribute is uninterpretable: '+key)
            if kind in ('esriFieldTypeString','esriFieldTypeGUID','esriFieldTypeGlobalID','esriFieldTypeXML') and not isinstance(value,str):
                raise ValueError('ArcGIS text attribute has the wrong type: '+key)
            domain=subtype.get(key) or field.get('domain') or {}
            if domain.get('type')=='codedValue' and value not in [item['code'] for item in domain['codedValues']]:
                raise ValueError('ArcGIS attribute is outside its published coded domain: '+key)
            if domain.get('type')=='range' and not domain['range'][0]<=value<=domain['range'][1]:
                raise ValueError('ArcGIS attribute is outside its published range: '+key)
