from __future__ import annotations

import json
import os
from pathlib import Path

from .contracts import Finding, Product, ProviderAssessment, digest
from .store import ROOT

CATALOGUE_VERSION = "2.0"


def product(id, category, name, publisher, version, adapter, endpoint, units, docs, *, kind="raster", **kw):
    limitations = [Finding(id=f"{id}:observation", rule="observation-age", severity="acknowledgement",
                           message="Acquisition dates and local accuracy must be read from the selected assets; absent values remain unknown.", basis=docs[0]),
                  Finding(id=f"{id}:publisher-integrity", rule="publisher-checksum", severity="acknowledgement",
                          message="Where a publisher provides no cryptographic checksum, ZEUS verifies the received snapshot and retains it; publisher-side byte identity cannot be independently proved.", basis=docs[0])]
    if not kw.get("license"):
        limitations.append(Finding(id=f"{id}:license-unknown",rule="source-license",severity="acknowledgement",message="The source licence has not been resolved. Acquisition does not establish redistribution permission.",basis=docs[0]))
    return Product(id=id, category=category, name=name, publisher=publisher, version=version, adapter=adapter,
                   endpoint=endpoint, units=units, kind=kind, attribution=kw.pop("attribution", publisher),
                   assessment=ProviderAssessment(disposition="requires_acknowledgement", findings=limitations, documentation=docs), **kw)


def registry() -> dict[str, Product]:
    from ..constants import CER_PIPELINES_LAYER_URL, CPCAD_LAYER_URL, CLSS_ABORIGINAL_LANDS_LAYER_URL
    p = []
    p.append(product("copernicus-glo30", "dem", "Copernicus DEM GLO-30", "Copernicus / DLR", "2021 public COG release; exact tile snapshot retained", "copernicus",
                     "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com", "m",
                     ["https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM",
                      "https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM.html"],
                     native_spacing={"x": 1/3600, "y": 1/3600, "units": "degree", "nominal_m": 30}, license="Copernicus DEM licence",
                     semantics={"surface": "DSM including vegetation and buildings", "vertical_reference": "EGM2008", "resolution_m": 30,"source_crs":"EPSG:4326"}))
    p.append(product('copernicus-glo90', 'dem', 'Copernicus DEM GLO-90', 'Copernicus / DLR', '2021 public COG release', 'copernicus',
                     'https://copernicus-dem-90m.s3.eu-central-1.amazonaws.com', 'm', ['https://registry.opendata.aws/copernicus-dem/', 'https://copernicus-dem-30m.s3.amazonaws.com/readme.html'],
                     native_spacing={'x':3/3600,'y':3/3600,'units':'degree','nominal_m':90}, license='Copernicus DEM licence',
                     semantics={'surface':'DSM including vegetation and buildings','vertical_reference':'EGM2008','resolution_m':90,'source_crs':'EPSG:4326','tile_spacing_code':'30'}))
    cop_basis='https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM'
    for item in p:
        item.observation_period={'primary_acquisition_start':'2011','primary_acquisition_end':'2015','precision':'year','local_infill_dates':'unknown; older elevation products were used for gap filling','basis':cop_basis}
        item.accuracy={'absolute_vertical_LE90_m':'<4','absolute_horizontal_CE90_m':'<6','relative_vertical_LE90_m':'<2 for slope <=20%; <4 above 20%, within a 1-degree geocell','local_AOI_accuracy':'unknown; product specification is not local validation','basis':cop_basis}
        item.native_spacing={'y':item.semantics['resolution_m']/30/3600,'units':'degree','nominal_m':item.semantics['resolution_m'],'longitude_spacing':'varies with latitude; selected tile spacings are listed in the asset inventory','basis':'https://copernicus-dem-30m.s3.amazonaws.com/readme.html'}
        item.semantics.update({'source_release_year':2021,'release_day':'unknown for this mirror; year documented by AWS registry','cog_conversion':'Publisher removes shared east/south edge samples and adds averaged display overviews; full-resolution source samples retained','provider_infill':'Edited DSM includes provider filling from older elevation products; source-specific infill dates are unknown'})
        item.assessment.documentation=sorted(set(item.assessment.documentation+[cop_basis,'https://registry.opendata.aws/copernicus-dem/','https://copernicus-dem-30m.s3.amazonaws.com/readme.html']))
    for resolution, dataset in [(1, "Digital Elevation Model (DEM) 1 meter"), (10, "National Elevation Dataset (NED) 1/3 arc-second"), (30, "National Elevation Dataset (NED) 1 arc-second")]:
        p.append(product(f"usgs-3dep-{resolution}m", "dem", f"USGS 3DEP {resolution} m", "USGS", "asset-specific", "tnm",
                         "https://tnmaccess.nationalmap.gov/api/v1/products", "m", ["https://www.usgs.gov/3d-elevation-program"], countries=["USA"], license="Public domain",
                         native_spacing={"nominal_m": resolution, "units": "m"}, semantics={"dataset_query": dataset, "resolution_m": resolution, "surface": "DTM; verify asset metadata"},
                         parameters={'catalogue':{'type':'enum','values':['staged_tiles','tnm'],'default':'staged_tiles'}} if resolution in (10,30) else {'catalogue':{'type':'enum','values':['project_index','tnm'],'default':'project_index'}}))
        if resolution==1:
            p[-1].assessment.documentation.extend(['https://www.usgs.gov/ngp-standards-and-specifications/3dep-product-metadata','https://pubs.usgs.gov/publication/tm11B7'])
        if resolution in (10,30):
            p[-1].semantics.update({'staged_prefix':f'https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/{13 if resolution==10 else 1}/TIFF/current','tile_code':'13' if resolution==10 else '1'})
            p[-1].assessment.documentation.append('https://www.usgs.gov/the-national-map-data-delivery/gis-data-download')
    for year, version in [(2021, "v200"), (2020, "v100")]:
        p.append(product(f"worldcover-{year}", "landcover", f"ESA WorldCover {year}", "ESA / VITO", version, "worldcover",
                         "https://esa-worldcover.s3.eu-central-1.amazonaws.com", "class_code", ["https://esa-worldcover.org/en/data-access"],
                         native_spacing={"x": 1/12000, "y": 1/12000, "units": "degree", "nominal_m": 10},
                         observation_period={"start": str(year), "end": str(year), "precision": "year"}, license="CC-BY-4.0",
                         accuracy={"global_overall_classification_accuracy_percent":76.7 if year==2021 else 74.4,"local_AOI_accuracy":"unknown; global assessment does not establish local accuracy","basis":"https://esa-worldcover.org/en/data-access"},
                         attribution=f"© ESA WorldCover project {year} / Contains modified Copernicus Sentinel data ({year}) processed by ESA WorldCover consortium",
                         semantics={"year": year,"source_crs":"EPSG:4326", "resolution_m": 10, "classes": {"10": "Tree cover", "20": "Shrubland", "30": "Grassland", "40": "Cropland", "50": "Built-up", "60": "Bare/sparse vegetation", "70": "Snow and ice", "80": "Permanent water", "90": "Herbaceous wetland", "95": "Mangroves", "100": "Moss and lichen"}, "nodata": 0}))
    for prop, unit, scale in [("soc", "dg/kg", "divide by 10 for g/kg"), ("clay", "g/kg", "divide by 10 for percent"), ("sand", "g/kg", "divide by 10 for percent"),
            ('bdod','cg/cm3','divide by 100 for kg/dm3'),('cec','mmol(c)/kg','divide by 10 for cmol(c)/kg'),
            ('cfvo','cm3/dm3','divide by 10 for volume percent'),('nitrogen','cg/kg','divide by 100 for g/kg'),
            ('ocd','hg/m3','divide by 10 for kg/m3'),('ocs','t/ha','divide by 10 for kg/m2'),
            ('phh2o','pH x 10','divide by 10 for pH in water'),('silt','g/kg','divide by 10 for mass percent')]:
        p.append(product(f"soilgrids-{prop}", "soil", f"SoilGrids {prop}", "ISRIC", "2.0 / retained service snapshot", "soilgrids",
                         "https://files.isric.org/soilgrids/latest/data", unit, ["https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html","https://files.isric.org/soilgrids/latest/data/README.md"],
                         native_spacing={"x": 250, "y": 250, "units": "m"}, license="CC-BY-4.0",
                         semantics={"property": prop, "stored_units": unit, "conversion": scale, 'conversion_applied':False, "resolution_m": 250,
                            'value_range':[0,140 if prop=='phh2o' else 1000 if prop in ('clay','sand','silt','cfvo') else None],
                            'meaning':'Modelled '+{'soc':'soil organic carbon concentration','clay':'clay mass fraction','sand':'sand mass fraction','silt':'silt mass fraction','bdod':'bulk density','cec':'cation exchange capacity buffered at pH 7','cfvo':'coarse fragment volume fraction','nitrogen':'total nitrogen concentration','ocd':'organic carbon density','ocs':'organic carbon stock','phh2o':'pH in water'}[prop]},
                         parameters={"depth": {"type": "enum", "values": ['0-30cm'] if prop=='ocs' else ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"], "default": '0-30cm' if prop=='ocs' else "0-5cm"},
                                     "statistic": {"type": "enum", "values": ["mean", "Q0.05", "Q0.5", "Q0.95"], "default": "mean"}}))
        p[-1].assessment.findings.append(Finding(id=prop+':model',rule='soil-model-interpretation',severity='acknowledgement',basis=p[-1].assessment.documentation[0],
            message='Values are model predictions in the documented encoded units; the listed unit conversion is not applied. Mean and quantiles are distinct products. Masked terrain and spatially uneven training data limit coverage and accuracy; this is not a local soil observation.'))
        if prop=='ocs':
            p[-1].assessment.findings.append(Finding(id='carbon-stock-method',rule='soil-carbon-stock',severity='acknowledgement',basis=p[-1].assessment.documentation[0],
                message='This stock model covers 0–30 cm and excludes organic layers above mineral soil. It is modelled from sample-level stock calculations; it cannot be reproduced by summing the separately modelled carbon-density grids.'))
    p.append(product("gem-pga-2023", "geohazard", "GEM PGA 475-year rock 2023.1", "GEM Foundation", "2023.1.0", "zenodo",
                     "https://zenodo.org/api/records/8409647", "g", ["https://zenodo.org/records/8409647"], release_date="2023-10-13",
                     native_spacing={"x": 0.05, "y": 0.05, "units": "degree", "nominal_source_points_m": 6000},
                     semantics={"filename": "GEM-GSHM_PGA-475y-rock_v2023.zip", "source_crs":"EPSG:4326", "return_period_years": 475, "exceedance_probability": 0.1, "exposure_years": 50, "reference_rock_vs30_m_s": [760, 800], "resolution_m": 6000}))
    for category, query in [("roads", '["highway"]'), ("railways", '["railway"]'), ("powerlines", '["power"~"^(line|minor_line)$"]'), ("waterways", '["waterway"]'), ("pipelines", '["man_made"="pipeline"]')]:
        p.append(product(f"osm-{category}", category, f"OpenStreetMap {category}", "OpenStreetMap contributors", "explicit Overpass snapshot", "overpass",
                         "https://overpass-api.de/api/interpreter", "not_applicable", ["https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL"], kind="vector", license="ODbL-1.0",
                         semantics={"query_filter": query, "geometry_scope": "Frozen root identities and versions; recursively retained relation descendants. Member paths, roles, source tags and versions retained. Member geometry is not implicitly assembled into polygons. Missing or cyclic descendants block acquisition.", "crs": "EPSG:4326"}))
    for id, cat, name, publisher, url in [("can-cer", "pipelines", "Canada CER pipelines", "Canada Energy Regulator", CER_PIPELINES_LAYER_URL),
                                        ("can-clss", "indigenous_lands", "Canada CLSS legislative boundaries", "Natural Resources Canada", CLSS_ABORIGINAL_LANDS_LAYER_URL)]:
        item = product(id, cat, name, publisher, "service snapshot", "arcgis", url, "not_applicable", [url], kind="vector", countries=["CAN"], license="Open Government Licence - Canada", semantics={"crs": "service metadata"})
        item.assessment.findings.append(Finding(id=f"{id}:snapshot", rule="service-snapshot", severity="acknowledgement", message="If historical queries are unavailable, retained batches prove the acquired extract but not an atomic publisher snapshot.", basis="https://developers.arcgis.com/rest/services-reference/enterprise/query-feature-service-layer/"))
        p.append(item)
    p.append(product('can-cpcad','protected_areas','Canada protected and conserved areas 2025','Environment and Climate Change Canada','December 2025 reference dataset','eccc_catalogue',
                     'https://data-donnees.az.ec.gc.ca/api/path_contents','not_applicable',
                     ['https://www.canada.ca/en/environment-climate-change/services/national-wildlife-areas/protected-conserved-areas-database.html','https://data-donnees.az.ec.gc.ca/data/species/protectrestore/canadian-protected-conserved-areas-database/Databases'],
                     kind='vector',countries=['CAN'],license='Open Government Licence - Canada',release_date='2026-03-25',observation_period={'reference_date':'2025-12-31','basis':'National reporting reference date; individual establishment dates retained as attributes'},
                     semantics={'catalogue_path':'/species/protectrestore/canadian-protected-conserved-areas-database/Databases','filename':'ProtectedConservedArea_2025.zip','language':'English attribute dictionary; original bilingual names retained','access_method':'Published file geodatabase; no live service batching',
                                'crs':'ESRI:102001','layer_by_status':{'protected_conserved':'ProtectedConservedArea_2025','delisted':'ProtectedConservedAreaDelisted_2025'},
                                'auxiliary_join':{'table':'ProtectedConservedAreaComment_2025','key':'ZONE_ID'},'status_meaning':'Protected/conserved and delisted records are separate selections; delisted areas are not current protected areas'},
                     parameters={'year':{'type':'enum','values':['2025'],'default':'2025'},'status':{'type':'enum','values':['protected_conserved','delisted'],'default':'protected_conserved'}}))
    p.append(product("can-nhn", "waterways", "Canada National Hydro Network", "Natural Resources Canada", "workunit release snapshot", "nhn",
                     "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/index/NHN_INDEX_WORKUNIT_LIMIT_2.zip", "not_applicable",
                     ["https://natural-resources.canada.ca/maps-tools-publications/maps/geospatial-products/national-hydro-network"], kind="vector", countries=["CAN"], license="Open Government Licence - Canada",semantics={'source_crs':'EPSG:4617'}))
    p.append(product("sentinel2-l2a", "imagery", "Sentinel-2 Level-2A", "Copernicus / ESA", "selected scene and processing baseline", "stac",
                     "https://planetarycomputer.microsoft.com/api/stac/v1", "surface_reflectance (encoded DN retained)",
                     ["https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_L2A_baseline.html", "https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a"], kind="imagery", license="Copernicus Sentinel data terms",
                     semantics={"collection": "sentinel-2-l2a", "quality_assets": ["SCL"], "band_metadata_required": True},
                     parameters={"start": {"type": "date", "required": True}, "end": {"type": "date", "required": True}, "scene_ids":{"type":"identifier_list","default":[]}, "bands": {"type": "list", "values": ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12"], "default": ["B02", "B03", "B04"]}, "max_cloud_cover": {"type": "number", "min": 0, "max": 100, "default": 100}}))
    p.append(product("era5-single-levels", "climate", "ERA5 hourly single levels", "ECMWF / Copernicus", "request and retained response", "cds",
                     "https://cds.climate.copernicus.eu/api", "per variable; CF metadata required", ["https://confluence.ecmwf.int/pages/viewpage.action?pageId=177484060", "https://cds.climate.copernicus.eu/how-to-api"], kind="climate", credentials=["CDSAPI_KEY"], license="Copernicus licence",
                     native_spacing={'x':.25,'y':.25,'units':'degree','nominal_model_resolution_km':31},
                     semantics={"dataset": "reanalysis-era5-single-levels", "calendar": "proleptic_gregorian", "preliminary": "ERA5T must be identified from returned metadata", 'provider_grid':[.25,.25], 'precipitation_time_bounds':'one hour ending at each UTC timestamp; 00 UTC includes the previous calendar day', 'grid_origin':'CDS provider interpolation to a regular latitude/longitude grid; original model resolution is distinct'},
                     parameters={"start": {"type": "date", "required": True}, "end": {"type": "date", "required": True}, "variables": {"type": "list", "values": ["2m_temperature", "total_precipitation", "10m_u_component_of_wind", "10m_v_component_of_wind", "surface_pressure"], "default": ["2m_temperature"]}}))
    p.append(product("worldpop-counts", "population", "WorldPop population counts", "WorldPop / University of Southampton", "selected release", "worldpop",
                     "https://www.worldpop.org/rest/data/pop/wpgp", "people/cell", ["https://www.worldpop.org/methods/top_down_constrained_vs_unconstrained/", "https://hub.worldpop.org/"], kind="population", license="CC-BY-4.0",
                     native_spacing={"x": 3/3600, "y": 3/3600, "units": "degree", "nominal_m_at_equator": 100},
                     semantics={"quantity": "count", "regrid": "forbidden", "model": "unconstrained", "native_values": True,"resolution_m":100,'source_crs':'EPSG:4326'},
                     parameters={"year": {"type": "integer", "min": 2000, "max": 2020, "required": True}, "country": {"type": "country", "required": True}}))
    p.append(product("swissalti3d-2m", "dem", "swissALTI3D 2 m", "Swiss Federal Office of Topography swisstopo", "selected tile releases", "stac_tiles",
                     "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items", "m",
                     ["https://www.swisstopo.admin.ch/en/height-model-swissalti3d", "https://docs.geo.admin.ch/download-data/stac-api/overview.html"], countries=["CHE"],
                     native_spacing={"x":2,"y":2,"units":"m"}, license="Swisstopo open geodata terms",
                     semantics={"resolution_m":2,"source_crs":"EPSG:2056","surface":"DTM","vertical_reference":"LN02 / EPSG:5728","asset_pattern":r"_2_2056_5728\.tif$"}))
    for surface in ("dtm", "dsm"):
        p.append(product(f"can-hrdem-{surface}", "dem", f"Canada HRDEM LiDAR {surface.upper()}", "Natural Resources Canada", "selected LiDAR project releases", "stac_tiles",
                         "https://datacube.services.geo.ca/pgstac/api/collections/hrdem-lidar/items", "m",
                         ["https://open.canada.ca/data/en/dataset/957782bf-847c-4644-a757-e383c0057995"], countries=["CAN"], license="OGL-Canada-2.0",
                         semantics={"surface":surface.upper(),"resolution_m":1,"asset_keys":[surface],"native_resolution":"per acquisition project"},
                         parameters={'acquisition_ids':{'type':'identifier_list','required':False}}))
    for name, cat in [("OpenRoads","roads"),("OpenRivers","waterways")]:
        p.append(product("os-"+name.lower(),cat,"OS "+("Open Roads" if cat == "roads" else "Open Rivers"),"Ordnance Survey","discovered release","os_downloads",
                         f"https://api.os.uk/downloads/v1/products/{name}","not_applicable",
                         ["https://docs.os.uk/os-apis/accessing-os-apis/os-downloads-api/technical-specification"],kind="vector",countries=["GBR"],license="Open Government Licence v3.0",
                         semantics={"format":"GeoPackage","crs":"EPSG:27700"}))
    for variable, unit in [("tavg","degC"),("tmin","degC"),("tmax","degC"),("prec","mm"),("srad","kJ m-2 day-1"),("wind","m s-1"),("vapr","kPa")]:
        p.append(product("worldclim-"+variable,"climate","WorldClim 2.1 monthly "+variable,"WorldClim / UC Davis","2.1 (1970–2000)","worldclim",
                         "https://geodata.ucdavis.edu/climate/worldclim/2_1/tiles/iso",unit,["https://worldclim.org/data/worldclim21.html"],kind="raster",
                         native_spacing={"x":1/120,"y":1/120,"units":"degree","nominal_m":1000},observation_period={"start":"1970","end":"2000","precision":"year"},
                         license="WorldClim terms of use",semantics={"variable":variable,"native_export":True,"resolution_m":1000,"expected_bands":12,"band_months":list(range(1,13)),"time_semantics":"monthly climatological mean; precipitation monthly total climatology",'source_crs':'EPSG:4326'},
                         parameters={"country":{"type":"country","required":True}}))
    for id, name, endpoint, license, meaning in [
        ('hydrorivers-v1','HydroRIVERS v1','https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_shp.zip','HydroSHEDS core product licence',
         'River reaches with catchment >=10 km2 or mean flow >=0.1 m3/s, derived from 15 arc-second HydroSHEDS; not a survey of every mapped waterway'),
        ('hydrolakes-v1','HydroLAKES v1 lake polygons','https://data.hydrosheds.org/file/hydrolakes/HydroLAKES_polys_v10_shp.zip','CC-BY-4.0',
         'Lake and reservoir shorelines >=10 hectares; modelled depth, volume and residence-time attributes are estimates')]:
        p.append(product(id,'waterways',name,'HydroSHEDS / McGill University','1.0','http',endpoint,'not_applicable',
                         ['https://www.hydrosheds.org/products/'+id.split('-')[0]],kind='vector',license=license,
                         semantics={'meaning':meaning,'crs':'EPSG:4326','coverage':'global archive'}))
    p.append(product('ghs-pop-2023a-1km','population','GHS-POP R2023A 1 km','European Commission Joint Research Centre','R2023A V1-0','ghs_pop',
                     'https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A','people/cell',
                     ['https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php','https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/copyright.txt'],
                     kind='population',license='European Commission reuse terms; source acknowledgement required',
                     native_spacing={'x':1000,'y':1000,'units':'m'},semantics={'quantity':'count','source_crs':'ESRI:54009','resolution_m':1000,'model':'GHSL dasymetric redistribution using built-up surface; not interchangeable with WorldPop constrained/unconstrained products','native_values':True},
                     parameters={'year':{'type':'enum','values':[str(y) for y in range(1975,2031,5)],'required':True}}))
    surface_water = product('jrc-gsw-occurrence-1.5','waterways','JRC Global Surface Water occurrence 1984–2024','EC JRC / Google','1.5','gsw',
                            'https://s3.waw4-1.cloudferro.com/swift/v1/global-surface-water/','percent',
                            ['https://global-surface-water.appspot.com/download'],license='Copernicus free-use terms; attribution and citation required',attribution='Source: EC JRC/Google; Pekel et al. (2016), doi:10.1038/nature20584',
                            observation_period={'start':'1984','end':'2024','precision':'year'},release_date='2026-08-26',native_spacing={'x':1/3600,'y':1/3600,'units':'degree','nominal_m':30},
                            semantics={'native_export':True,'source_crs':'EPSG:4326','resolution_m':30,'nodata':255,'classes':{str(i):str(i)+' percent' for i in range(101)},'meaning':'Water occurrence frequency from the publisher time series; not an individual image or current water boundary'})
    surface_water.assessment.findings.append(Finding(id='jrc-gsw:collection-registration',rule='source-coregistration',severity='acknowledgement',message='Version 1.5 combines Landsat Collections 1 and 2. The publisher reports spatially variable residual offsets, sometimes >=30 m; full backprocessing is pending.',basis='https://global-surface-water.appspot.com/download'))
    p.append(surface_water)
    # Additional providers are reviewed, versioned records, never user-supplied URLs.
    csi=product('cgiar-srtm-4.1','dem','CGIAR-CSI SRTM 90 m version 4.1','CIAT / CGIAR-CSI','4.1; archive readme and grid checked','cgiar_srtm',
        'https://srtm.csi.cgiar.org/wp-content/uploads/files/srtm_5x5/TIFF','m',
        ['https://bigdata.cgiar.org/srtm-90m-digital-elevation-database/','https://srtm.csi.cgiar.org/wp-content/uploads/files/Reuteretal2007.pdf','https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm'],
        license='CGIAR-CSI/CIAT terms: noncommercial use with attribution; commercial use and redistribution require written permission',
        attribution='Jarvis, Reuter, Nelson and Guevara (2008), Hole-filled seamless SRTM data V4, CIAT',
        native_spacing={'x':3/3600,'y':3/3600,'units':'degree','nominal_m_at_equator':90},
        observation_period={'primary_radar_start':'2000-02-11','primary_radar_end':'2000-02-22','infill_observation_dates':'unknown; provider interpolated voids and auxiliary elevations'},
        semantics={'source_crs':'EPSG:4326','resolution_m':90,'surface':'Radar-derived surface elevations; vegetation/buildings can affect heights; not a bare-earth DTM','vertical_reference':'EGM96','nodata':-32768,'provider_processing':'CGIAR-CSI interpolation and shoreline masking; retained 4.1 release includes grid/shoreline fixes','latitude_tile_range':[-60,60],'release_day':'unknown; HTTP Last-Modified is object metadata, not a scientifically verified release date'})
    csi.assessment.findings.extend([
        Finding(id='cgiar-use-terms',rule='source-license',severity='acknowledgement',message='The provider restricts commercial use and redistribution without written permission. This acquisition is a local copy; exporting a provenance bundle does not grant permission to share its source data.',basis=csi.assessment.documentation[0]),
        Finding(id='cgiar-infill',rule='provider-processing',severity='acknowledgement',message='Source voids were interpolated by CGIAR-CSI and shorelines were masked. The tile archive does not identify every interpolated cell; source observation dates and local accuracy there remain unknown.',basis=csi.assessment.documentation[0])])
    p.append(csi)
    census_layers={
        'alaska_native_regional_corporations':{'id':32,'name':'Alaska Native Regional Corporations'},
        'tribal_subdivisions':{'id':34,'name':'Tribal Subdivisions'},
        'federal_reservations':{'id':36,'name':'Federal American Indian Reservations'},
        'off_reservation_trust_lands':{'id':38,'name':'Off-Reservation Trust Lands'},
        'state_reservations':{'id':40,'name':'State American Indian Reservations'},
        'hawaiian_home_lands':{'id':42,'name':'Hawaiian Home Lands'},
        'alaska_native_village_statistical_areas':{'id':44,'name':'Alaska Native Village Statistical Areas'},
        'oklahoma_tribal_statistical_areas':{'id':46,'name':'Oklahoma Tribal Statistical Areas'},
        'state_designated_tribal_statistical_areas':{'id':48,'name':'State Designated Tribal Statistical Areas'},
        'tribal_designated_statistical_areas':{'id':50,'name':'Tribal Designated Statistical Areas'},
        'joint_use_areas':{'id':52,'name':'American Indian Joint-Use Areas'}}
    p.append(product('census-aiannha-2026','indigenous_lands','Census American Indian, Alaska Native and Native Hawaiian areas 2026','United States Census Bureau','ACS 2026 geographic vintage','arcgis',
        'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_ACS2026/MapServer','not_applicable',
        ['https://tigerweb.geo.census.gov/tigerwebmain/TIGERweb_restmapservice.html','https://tigerweb.geo.census.gov/tigerwebmain/TIGERweb_geography_details.html','https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_ACS2026/MapServer'],
        kind='vector',countries=['USA'],license='United States government data; cite United States Census Bureau',
        observation_period={'reference_date':'2026-01-01','meaning':'Legal/statistical geographic vintage; not a simultaneous boundary survey or a population count'},
        semantics={'crs':'service metadata','layers_by_parameter':{'parameter':'area_type','layers':census_layers},'meaning':'Separate legal and statistical area types used by the Census Bureau. Selected type retains its provider definition; types are not silently combined.'},
        parameters={'area_type':{'type':'enum','values':list(census_layers),'default':'federal_reservations'}}))
    p[-1].assessment.findings.append(Finding(id='census-geographic-vintage',rule='provider-coverage',severity='acknowledgement',
        message='This is the selected Census legal/statistical area type and geographic vintage. It is not a complete map of every Indigenous territory. The service does not assure an atomic historical query snapshot; exact returned batches are retained.',
        basis='https://tigerweb.geo.census.gov/tigerwebmain/TIGERweb_geography_details.html'))
    for geometry in ('lines','nodes'):
        endpoint='https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_North_American_Rail_Network_'+geometry.title()+'/FeatureServer/0'
        entry=product('fra-narn-'+geometry,'railways','North American Rail Network '+geometry,'Federal Railroad Administration / Bureau of Transportation Statistics','service data edit resolved at discovery','arcgis',endpoint,'not_applicable',
            ['https://catalog.data.gov/dataset/north-american-rail-network-'+geometry,'https://railroads.dot.gov/rail-network-development/maps-and-data/maps-geographic-information-system/maps-geographic',endpoint],
            kind='vector',countries=['USA','CAN','MEX'],license='United States government work; unrestricted public use',
            semantics={'crs':'service metadata','geometry_role':'rail network line with ownership/trackage attributes' if geometry=='lines' else 'network node with geographic and topology identifiers','documented_mapping_scale':'1:24,000 or better within USA; this is not a measured local accuracy'},
            observation_period={'value':'unknown','reason':'Service edit time is reported separately and is not the observation date of each feature'})
        entry.assessment.findings.append(Finding(id='fra-snapshot',rule='service-snapshot',severity='acknowledgement',message='The service has no assured atomic historical snapshot. Retained batches and editing timestamps establish the acquired extract; mapping completeness is a separate claim.',basis=endpoint))
        p.append(entry)
    from .conservation_products import products as conservation_products
    p.extend(conservation_products(product))
    from .landcover_products import products as landcover_products
    p.extend(landcover_products(product))
    os_terrain_doc='https://docs.os.uk/os-downloads/products/land-and-terrain-portfolio/os-terrain-50/'
    p.append(product('os-terrain50','dem','OS Terrain 50 native height grid','Ordnance Survey','Exact published annual release resolved during discovery','os_terrain50',
        'https://api.os.uk/downloads/v1/products/Terrain50','m',
        [os_terrain_doc+'os-terrain-50-overview',os_terrain_doc+'os-terrain-50-technical-specification/grid-data',os_terrain_doc+'os-terrain-50-technical-specification/metadata'],
        countries=['GBR'],license='Open Government Licence v3.0',native_spacing={'x':50,'y':50,'units':'m','grid':'200 x 200 samples per 10 km tile; pixel centres'},
        accuracy={'documented_grid_height_RMSE_m':4,'local_AOI_accuracy':'unknown; published product test does not establish local error','basis':os_terrain_doc+'os-terrain-50-overview'},
        semantics={'source_crs':'EPSG:27700','resolution_m':50,'surface':'DTM','stored_height_precision_m':.1,
                   'coverage':'Great Britain; not Northern Ireland','vertical_reference':'Resolved from each selected tile ISO metadata; different vertical references cannot be silently merged'}))
    p[-1].assessment.findings.append(Finding(id='os-national-package-discovery',rule='discovery-storage',severity='acknowledgement',basis=os_terrain_doc+'os-terrain-50-technical-specification/grid-data',
        message='The provider distributes one national grid archive. Discovery retains this package to review actual tile survey dates, metadata and dependencies. The full source package is kept for replay; publisher styling statistics are not used as scientific validation.'))
    from .hwsd import products as hwsd_products
    p.extend(hwsd_products(product))
    extra = ROOT / "docs" / "datasets" / "providers.json"
    if extra.exists():
        p.extend(Product.model_validate(x) for x in json.loads(extra.read_text(encoding="utf-8")))
    result = {item.id: item for item in p}
    if len(result) != len(p):
        raise ValueError("Duplicate product identities")
    return result


def parameters(product: Product, supplied: dict) -> dict:
    from datetime import date
    unknown = set(supplied) - set(product.parameters)
    if unknown:
        raise ValueError(f"Unsupported parameters: {sorted(unknown)}")
    resolved = {}
    for key, spec in product.parameters.items():
        value = supplied.get(key, spec.get("default"))
        if value is None:
            if spec.get("required"):
                raise ValueError(f"{product.name} requires {key}")
            continue
        kind = spec["type"]
        if kind == "date":
            value = date.fromisoformat(str(value)).isoformat()
        elif kind == "country":
            import pycountry
            if not isinstance(value, str) or pycountry.countries.get(alpha_3=value.upper()) is None:
                raise ValueError("Country must be an ISO 3166 alpha-3 code")
            value = value.upper()
        elif kind == "enum" and value not in spec["values"]:
            raise ValueError(f"Invalid {key}")
        elif kind == "list":
            if not isinstance(value, list) or not value or any(v not in spec["values"] for v in value):
                raise ValueError(f"Invalid {key}")
            value = sorted(set(value))
        elif kind == 'identifier_list':
            import re
            if value==[] and not spec.get('required'): continue
            if not isinstance(value,list) or not 1<=len(value)<=2048 or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}',v) for v in value):
                raise ValueError('Acquisition identifiers must be a bounded list of exact catalogue IDs')
            value=sorted(set(value))
        elif kind in ("number", "integer"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "integer" and not isinstance(value, int)) or not spec["min"] <= value <= spec["max"]:
                raise ValueError(f"Invalid {key}")
        resolved[key] = value
    if "start" in resolved and resolved["start"] > resolved["end"]:
        raise ValueError("Start date must not follow end date")
    return resolved


def public_registry(project_countries=None):
    from .qualification import attach_verification
    values = [attach_verification(p).model_dump(mode="json") for p in registry().values()]
    for p in values:
        p["access_ready"] = all(os.environ.get(c) for c in p["credentials"])
    audit = ROOT / "docs" / "datasets" / "catalogue-assessment.json"
    references = []
    if audit.exists():
        references = [{"dataset": row["dataset"], "country": row["country"], "product_ids": row["product_ids"], "disposition": row["disposition"]} for row in json.loads(audit.read_text(encoding="utf-8"))["entries"]]
    defaults = {}
    for p in values:
        eligible_country = 'WLD' in p['countries'] or bool(set(project_countries or []) & set(p['countries']))
        if eligible_country and p['access_ready'] and p['assessment']['disposition'] not in ('excluded', 'unavailable'):
            defaults.setdefault(p['category'], p['id'])
    return {"schema_version": "zeus.acquisition/2.0", "catalogue_version": CATALOGUE_VERSION, "products": values, "catalogue_references": references,
            'project_countries': project_countries or [], 'default_product_by_category': defaults,
            "default_selection_policy": "Registry order among accessible products whose declared country coverage intersects the project's country hints, or worldwide products. Unknown country defaults only to worldwide products. Country hints are not measured AOI coverage. Exact products and parameters are reviewed before confirmation; execution never reselects."}
