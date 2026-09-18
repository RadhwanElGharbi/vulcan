"""Distinct Impact Observatory model releases; no inter-release substitution."""
from .contracts import Finding


def products(product):
    endpoint='https://planetarycomputer.microsoft.com/api/stac/v1'
    base={'1':'Water','2':'Trees','4':'Flooded vegetation','5':'Crops','7':'Built area','8':'Bare ground','9':'Snow/ice','10':'Clouds'}
    result=[]
    releases=[('io-lulc-2020-10class','io-lulc','2020 original 10-class model',range(2020,2021),{'3':'Grass','6':'Scrub/shrub'},'io-lulc-model-001-v01-composite-v03-supercell-v02-clip-v01/'),
              ('io-lulc-annual-v1','io-lulc-9-class','Annual 9-class V1',range(2017,2023),{'11':'Rangeland'},'nine-class/'),
              ('io-lulc-annual-v2','io-lulc-annual-v02','Annual 9-class V2',range(2017,2024),{'11':'Rangeland'},'io-annual-lulc-v02/')]
    for pid,collection,version,years,extra,prefix in releases:
        doc=endpoint+'/collections/'+collection
        entry=product(pid,'landcover','Impact Observatory / Esri '+version,'Impact Observatory / Esri / Microsoft',version,'stac_landcover',endpoint,'class_code',[doc],
            license='CC-BY-4.0',attribution='Impact Observatory, Esri and Microsoft; contains modified Copernicus Sentinel data',
            native_spacing={'x':10,'y':10,'units':'m','grid':'Native UTM grid retained separately for each provider tile'},
            accuracy={'average_accuracy_percent':'>75' if collection!='io-lulc' else 'unknown','local_AOI_accuracy':'unknown; provider average is not local validation','basis':doc},
            semantics={'collection':collection,'classes':{**base,**extra},'nodata':0,'expected_bands':1,'resolution_m':10,'native_export':True,
                       'asset_prefix':'https://ai4edataeuwest.blob.core.windows.net/io-lulc/'+prefix,
                       'non_surface_classes':{'10':'Clouds: underlying surface class is unknown'},
                       'meaning':'Provider modelled annual land-cover classification composite; not an individual satellite acquisition',
                       'model_relationship':'Original 10-class model, 9-class V1 and V2 are separate releases. V2 changes the model and aligns the grid to ESA UTM tiling.'},
            parameters={'year':{'type':'enum','values':[str(y) for y in years],'default':'2020'}})
        entry.assessment.findings.append(Finding(id='annual-classification-model',rule='landcover-model-interpretation',severity='acknowledgement',basis=doc,
            message='This is a provider-produced annual classification composite. Individual observation dates and local accuracy are unknown. Clouds are a documented class with unknown underlying land cover. Model releases and their class legends are not interchangeable.'))
        if collection!='io-lulc-annual-v02':
            entry.assessment.findings.append(Finding(id='retirement-notice',rule='provider-availability',severity='acknowledgement',basis=doc,
                message='The publisher announced retirement of this older collection in December 2024. Acquisition requires the exact historical objects to remain available; ZEUS will not substitute a newer model. Preserved inputs remain replayable.'))
        result.append(entry)
    return result
