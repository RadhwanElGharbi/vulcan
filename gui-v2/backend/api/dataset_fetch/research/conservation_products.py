"""Distinct conservation products with publisher-owned service identities."""
from .contracts import Finding


def products(make):
    result=[]
    for key,layer,name,status in [
        ('nor-protected-areas',0,'naturvern_omrade','established'),
        ('nor-proposed-protected-areas',4,'foreslatt_naturvern_omrade','proposed')]:
        doc='https://kartkatalog.miljodirektoratet.no/Dataset/Details/'+('0' if status=='established' else '1')
        endpoint=f'https://kart.miljodirektoratet.no/arcgis/rest/services/vern/MapServer/{layer}'
        entry=make(key,'protected_areas','Norway '+status+' conservation areas','Miljødirektoratet','retained Naturbase service snapshot','arcgis',endpoint,'not_applicable',
            [doc,'https://kartkatalog.miljodirektoratet.no/MapService/Details/vern','https://data.norge.no/nb/data-services/1ca2cf81-3207-35a3-8448-c24dc176a128/rest-api',endpoint],
            kind='vector',countries=['NOR','SJM'],license='Norwegian Licence for Open Government Data (NLOD) 2.0',attribution='Kilde: Miljødirektoratet',
            observation_period={'value':'unknown','reason':'Feature designation or capture dates describe individual records; the service has no simultaneous observation date.'},
            accuracy={'value':'unknown','reason':'No uniform local positional accuracy is established by this service snapshot; retain any feature-level quality fields.'},
            semantics={'expected_layer_name':name,'expected_geometry_type':'esriGeometryPolygon','source_crs':'EPSG:25833','coverage':'Norway, Svalbard and Jan Mayen; selected layer only',
                'designation_status':status,'feature_date_meaning':'vernedato/foerstegangVernet are legal designation dates; datafangstdato is a source capture date when present',
                'meaning':'Provider conservation polygons with original designations and identifiers. Proposed areas do not establish current protection.'})
        entry.assessment.findings.append(Finding(id=key+':status',rule='designation-status',severity='acknowledgement',basis=doc,
            message='This selection contains '+status+' conservation areas only. Overlapping designations remain separate records. The layer is not a complete inventory of every environmental restriction.'))
        result.append(entry)
    for short,label,identity in [('nnr','National','ab7bfd86f5b347df8d47fc9bfab80caf'),('lnr','Local','b1d690ac6dd54c15bdd2d341b686ecd7')]:
        key='eng-'+short
        endpoint=f'https://services.arcgis.com/JJzESW51TqeY9uat/arcgis/rest/services/{label}_Nature_Reserves_England/FeatureServer/0'
        item='https://www.arcgis.com/sharing/rest/content/items/'+identity+'?f=json'
        doc='https://naturalengland-defra.opendata.arcgis.com/datasets/'+identity+'/about'
        entry=make(key,'protected_areas',label+' Nature Reserves (England)','Natural England','retained service data edit snapshot','arcgis',endpoint,'not_applicable',
            [doc,item,'https://www.gov.uk/guidance/how-to-access-natural-englands-maps-and-data',endpoint],kind='vector',countries=['GBR'],license='Open Government Licence v3.0',
            attribution='© Natural England copyright. Contains Ordnance Survey data © Crown copyright and database right 2024 (publisher item attribution).',
            observation_period={'value':'unknown','reason':'The service data edit time is not the field observation or legal designation date.'},
            semantics={'expected_layer_name':label+' Nature Reserves (England) © Natural England','expected_geometry_type':'esriGeometryPolygon','source_crs':'EPSG:27700',
                       'coverage':'England only; no inference of absence in Scotland, Wales or Northern Ireland','designation_type':label+' Nature Reserve',
                       'provider_item':identity,'meaning':'Original reserve polygons and identifiers; distinct designation types are not merged.'})
        entry.assessment.findings.append(Finding(id=key+':extent',rule='provider-coverage',severity='acknowledgement',basis=doc,
            message='This product covers '+label.lower()+' nature reserves in England only. Empty results outside England do not establish an absence of protected areas. Other designations are separate products.'))
        result.append(entry)
    for entry in result:
        entry.assessment.findings.append(Finding(id=entry.id+':snapshot',rule='service-snapshot',severity='acknowledgement',basis=entry.endpoint,
            message='The service does not assure an atomic historical snapshot. Exact returned batches, source IDs, schema and available edit metadata are retained; provider completeness and real-world completeness remain separate claims.'))
    return result
