from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

from .contracts import Asset, Finding, digest
from .planning import split_bounds


def asset(url, *, id=None, role="data", metadata=None, size=None):
    return Asset(id=id or url, url=url, filename=PurePosixPath(urlsplit(url).path).name or "response.json", role=role, metadata=metadata or {}, size=size)


def discover(product, params, aoi, as_of, transport):
    assets, snapshots, findings = [], [], []
    boxes = list(split_bounds(aoi))
    adapter = product.adapter
    if adapter == 'cgiar_srtm':
        tiles=set()
        for w,s,e,n in boxes:
            if s < -60 or n > 60:
                findings.append(Finding(id='cgiar-latitude-range',rule='catalogue-coverage',severity='acknowledgement',message='The requested AOI extends beyond the CGIAR tile latitude range. Measured output gaps require separate approval.'))
            south,north=max(-60,s),min(60,n)
            if south>=north: continue
            for column in range(math.floor((w+180)/5)+1,math.ceil((e+180)/5)+1):
                for row in range(math.floor((60-north)/5)+1,math.ceil((60-south)/5)+1):
                    tiles.add((column,row))
        for column,row in sorted(tiles):
            name=f'srtm_{column:02}_{row:02}'
            west,north=-180+(column-1)*5,60-(row-1)*5
            metadata={'archive_product':'CGIAR-CSI SRTM 4.1','required_tiff':name+'.tif','source_crs':'EPSG:4326',
                'expected_native_grid':{'width':6000,'height':6000,'x_spacing':1/1200,'y_spacing':1/1200,'origin':[west,north],'origin_tolerance':1e-9,'nodata':-32768,'datatype':'Int16','bands':1,'basis':'Retained CGIAR-CSI version 4.1 readme, GeoTIFF header and five-degree tile identity'}}
            try:
                assets.append(transport.inspect(asset(product.endpoint+'/'+name+'.zip',id=name,metadata=metadata)))
            except ValueError as exc:
                if 'HTTP 404' not in str(exc): raise
                findings.append(Finding(id='absent:'+name,rule='catalogue-coverage',severity='acknowledgement',message='No provider tile '+name+' was found; no other DEM will be substituted.',evidence={'bbox':[west,north-5,west+5,north]}))
    elif adapter in ("copernicus", "worldcover"):
        step = 1 if adapter == "copernicus" else 3
        tiles = set()
        for w, s, e, n in boxes:
            tiles.update((lat, lon) for lat in range(math.floor(s/step)*step, math.ceil(n/step)*step, step) for lon in range(math.floor(w/step)*step, math.ceil(e/step)*step, step))
        for lat, lon in sorted(tiles):
            if adapter == "copernicus":
                spacing = product.semantics.get('tile_spacing_code', '10')
                name = f"Copernicus_DSM_COG_{spacing}_{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00_DEM"
                url = f"{product.endpoint}/{name}/{name}.tif"
            else:
                year, version = product.semantics["year"], product.version
                name = f"ESA_WorldCover_10m_{year}_{version}_{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}_Map"
                url = f"{product.endpoint}/{version}/{year}/map/{name}.tif"
            try:
                metadata={"footprint": [lon, lat, lon+step, lat+step]}
                if adapter == 'copernicus':
                    latitude=abs(lat+.5)
                    factor=next(factor for limit,factor in [(50,1),(60,1.5),(70,2),(80,3),(85,5),(91,10)] if latitude<limit)
                    base=product.semantics['resolution_m']/30/3600
                    metadata['expected_native_grid']={'x_spacing':base*factor,'y_spacing':base,'width':round(1/(base*factor)),'height':round(1/base),'basis':'https://copernicus-dem-30m.s3.amazonaws.com/readme.html'}
                assets.append(transport.inspect(asset(url, id=name, metadata=metadata)))
            except ValueError as exc:
                if "HTTP 404" not in str(exc):
                    raise
                findings.append(Finding(id=f"absent:{name}", rule="catalogue-coverage", severity="acknowledgement", message=f"Provider has no tile {name}; actual AOI gaps will require review.", evidence={"url": url, "bbox": [lon, lat, lon+step, lat+step]}))
    elif adapter == "tnm":
        if params.get('catalogue') == 'project_index':
            from .usgs_projects import discover_projects
            return discover_projects(product,boxes,transport)
        if params.get('catalogue') == 'staged_tiles':
            from .usgs import discover_seamless
            return discover_seamless(product,boxes,transport)
        found = {}
        for bbox in boxes:
            offset, total = 0, None
            query_ids = set()
            while total is None or offset < total:
                value, receipt = transport.json(product.endpoint, params={"datasets": product.semantics["dataset_query"], "bbox": ",".join(map(str, bbox)), "outputFormat": "JSON", "max": 100, "offset": offset})
                snapshots.append(receipt)
                items = value.get("items")
                if not isinstance(items, list) or not isinstance(value.get("total"), int):
                    raise ValueError("TNM did not return a complete typed inventory")
                if total is not None and value["total"] != total:
                    raise ValueError("TNM inventory changed during pagination")
                total = value["total"]
                if total > 2048:
                    raise ValueError("TNM selection exceeds 2048 assets; reduce AOI")
                if not items and offset < total:
                    raise ValueError("TNM pagination terminated prematurely")
                for item in items:
                    key = item.get("sourceId") or item.get("id")
                    if not key or not item.get("downloadURL"):
                        raise ValueError("TNM product lacks a stable identifier or download URL")
                    if key in query_ids:
                        raise ValueError("TNM duplicated an item within a paginated inventory")
                    query_ids.add(key)
                    if key in found and found[key] != item:
                        raise ValueError("TNM returned conflicting versions of a product")
                    found[key] = item
                offset += len(items)
            if len(query_ids) != total:
                raise ValueError("TNM inventory count does not match unique planned assets")
        for key, item in sorted(found.items()):
            # Oldest first, newest last gives explicit deterministic overlap precedence.
            identity = f"{item.get('dateUpdated', '')}:{key}"
            assets.append(transport.inspect(asset(item["downloadURL"], id=identity, metadata=item)))
    elif adapter == "zenodo":
        value, receipt = transport.json(product.endpoint)
        snapshots.append(receipt)
        matches = [x for x in value.get("files", []) if x.get("key") == product.semantics["filename"]]
        if len(matches) != 1:
            raise ValueError("Zenodo record does not contain the expected unique archive")
        item = matches[0]
        entry = asset(item["links"]["self"], id=item["key"], size=item.get("size"), metadata={"record": value.get("id"), "license": value.get("metadata", {}).get("license")})
        entry.filename = item["key"]
        checksum = item.get("checksum", "")
        if ":" not in checksum:
            raise ValueError("Zenodo publisher checksum is missing")
        entry.hash_algorithm, entry.expected_hash = checksum.split(":", 1)
        assets.append(entry)
    elif adapter == "arcgis":
        layer_choice=product.semantics.get('layers_by_parameter')
        if layer_choice:
            chosen=layer_choice['layers'][params[layer_choice['parameter']]]
            product.endpoint=product.endpoint.rstrip('/')+'/'+str(chosen['id'])
            product.semantics['selected_layer']=chosen
            product.semantics.pop('layers_by_parameter')
        metadata, receipt = transport.json(product.endpoint, params={"f": "json"})
        snapshots.append(receipt)
        if layer_choice and metadata.get('name')!=chosen['name']:
            raise ValueError('The provider layer identity changed; the selected product cannot be confirmed')
        if product.semantics.get('expected_layer_name') and metadata.get('name')!=product.semantics['expected_layer_name']:
            raise ValueError('The provider layer identity changed; the selected product cannot be confirmed')
        if product.semantics.get('expected_geometry_type') and metadata.get('geometryType')!=product.semantics['expected_geometry_type']:
            raise ValueError('The provider geometry type changed from the registered product contract')
        if not metadata.get("fields") or not (metadata.get("extent", {}).get("spatialReference") or metadata.get("sourceSpatialReference")):
            raise ValueError("ArcGIS layer lacks schema or source CRS")
        if metadata.get('hasM') or metadata.get('hasCurves'):
            raise ValueError('This ArcGIS layer requires a native measured/curved geometry client; GeoJSON cannot faithfully preserve it')
        product.semantics.update({'provider_native_crs':metadata.get('sourceSpatialReference') or metadata.get('extent',{}).get('spatialReference'),
            'provider_response':'GeoJSON EPSG:4326 with every field and source Z values if present; provider coordinate conversion precedes retained response',
            'provider_geometry_type':metadata.get('geometryType'),'attribute_schema':metadata['fields']})
        edited=metadata.get('editingInfo',{}).get('dataLastEditDate') or metadata.get('editingInfo',{}).get('lastEditDate')
        if edited:
            product.version='service data edit '+datetime.fromtimestamp(edited/1000,timezone.utc).isoformat()
            product.semantics['provider_data_edit_time']=datetime.fromtimestamp(edited/1000,timezone.utc).isoformat()
        findings.append(Finding(id='arcgis-response-conversion',rule='provider-conversion',severity='acknowledgement',basis='https://developers.arcgis.com/rest/services-reference/enterprise/query-feature-service-layer/',
            message='The provider returns GeoJSON in WGS84. Its internal coordinate conversion is not reproducible by ZEUS from native source data; replay starts from the exact preserved provider response. Schema, source IDs and the requested response CRS are recorded.'))
        if not metadata.get('objectIdField'):
            oid = [field['name'] for field in metadata['fields'] if field.get('type') == 'esriFieldTypeOID']
            if len(oid) != 1:
                raise ValueError('ArcGIS object identity field is missing or ambiguous')
            metadata['objectIdField'] = oid[0]
        historic = metadata.get("advancedQueryCapabilities", {}).get("supportsQueryWithHistoricMoment", False)
        common = {"f": "json", "where": "1=1", "inSR": 4326, "geometryType": "esriGeometryEnvelope", "spatialRel": "esriSpatialRelIntersects"}
        if historic:
            common["historicMoment"] = int(datetime.fromisoformat(as_of.replace("Z", "+00:00")).timestamp()*1000)
        ids = set()
        def inventory(bbox, depth=0):
            query = {**common, "geometry": ",".join(map(str, bbox))}
            count, receipt = transport.json(product.endpoint+"/query", params={**query, "returnCountOnly": "true"})
            snapshots.append(receipt)
            value, receipt = transport.json(product.endpoint+"/query", params={**query, "returnIdsOnly": "true"})
            snapshots.append(receipt)
            returned = value.get("objectIds")
            if returned is None and "objectIds" in value and count.get("count") == 0:
                returned = []  # ArcGIS documents null as a possible empty ID result.
            total = count.get('count')
            if isinstance(total, bool) or not isinstance(total, int) or total < 0 or total > 1_000_000:
                raise ValueError('ArcGIS count inventory is invalid or exceeds the feature limit')
            if isinstance(returned, list) and len(set(returned)) != len(returned):
                raise ValueError('ArcGIS ID query duplicated source identities')
            if isinstance(returned, list) and not value.get('exceededTransferLimit') and total == len(returned):
                return set(returned)
            if depth >= 12:
                raise ValueError('ArcGIS cannot provide a complete bounded ID partition')
            w,s,e,n = bbox
            parts = [(w,s,(w+e)/2,n),((w+e)/2,s,e,n)] if e-w >= n-s else [(w,s,e,(s+n)/2),(w,(s+n)/2,e,n)]
            combined = set().union(*(inventory(part,depth+1) for part in parts))
            if len(combined) != total:
                raise ValueError('ArcGIS partition union does not reconcile with the parent inventory')
            return combined
        for bbox in boxes:
            ids.update(inventory(bbox))
        expected = sorted(ids)
        snapshots.append({"arcgis": {"metadata": metadata, "expected_ids": expected, "historicMoment": common.get("historicMoment"), "editingInfo": metadata.get("editingInfo"), "object_id_field": metadata.get("objectIdField")}})
        batch_size = min(200, metadata.get('maxRecordCount', 200))
        if not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError('ArcGIS feature service limit is invalid')
        for index in range(0, len(expected), batch_size):
            batch = expected[index:index+batch_size]
            request={'f':'geojson','objectIds':','.join(map(str,batch)),'outFields':'*','outSR':4326,'returnGeometry':'true','returnZ':'true' if metadata.get('hasZ') else 'false'}
            if common.get('historicMoment'): request['historicMoment']=common['historicMoment']
            assets.append(Asset(id=f"batch:{index//batch_size:06d}", url=product.endpoint+"/query", filename=f"batch-{index//batch_size:06d}.json", metadata={"ids": batch, "historicMoment": common.get("historicMoment"), "object_id_field": metadata.get("objectIdField"),'request':request}))
    elif adapter == "overpass":
        clauses = []
        for w, s, e, n in boxes:
            for kind in ("way", "relation"):
                clauses.append(f'{kind}{product.semantics["query_filter"]}({s},{w},{n},{e});')
        prefix=f'[out:json][timeout:180][date:"{as_of}"];'
        discovery_query=prefix+'('+''.join(clauses)+');out meta;'
        value,receipt=transport.json(product.endpoint,method='POST',data={'data':discovery_query})
        if not isinstance(value.get('elements'),list) or not value.get('osm3s',{}).get('timestamp_osm_base'):
            raise ValueError('Overpass root discovery lacks a typed snapshot response')
        roots=[];identities=set()
        for element in value['elements']:
            identity=(element.get('type'),element.get('id'))
            if identity[0] not in ('way','relation') or type(identity[1]) is not int or identity[1]<=0 or identity in identities or not element.get('version') or not element.get('timestamp'):
                raise ValueError('Overpass root inventory is duplicated or lacks mandatory identity/version evidence')
            identities.add(identity)
            roots.append({key:element[key] for key in ('type','id','version','timestamp')})
        if len(roots)>100_000: raise ValueError('Overpass selection exceeds the 100,000-root limit')
        roots.sort(key=lambda r:(r['type'],r['id']))
        clauses=[kind+'(id:'+','.join(str(row['id']) for row in roots if row['type']==kind)+');' for kind in ('way','relation') if any(row['type']==kind for row in roots)]
        query=prefix+'('+''.join(clauses)+');(._;>>;);out meta geom;' if roots else None
        snapshots.extend([receipt,{"overpass": {"query": query,'expected_roots':roots, "snapshot": as_of, "endpoint": product.endpoint}}])
        product.semantics['frozen_root_count']=len(roots)
    elif adapter == "stac_tiles":
        from shapely.geometry import shape
        seen = set()
        for bbox in boxes:
            url, query = product.endpoint, {"bbox": ",".join(map(str, bbox)), "limit": 100}
            visited = set()
            while url:
                marker = (url, json.dumps(query, sort_keys=True))
                if marker in visited:
                    raise ValueError("STAC pagination loop")
                visited.add(marker)
                payload, receipt = transport.json(url, params=query)
                snapshots.append(receipt)
                if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
                    raise ValueError("Invalid STAC tile inventory")
                for item in payload["features"]:
                    if item["id"] in seen:
                        continue  # Split AOI queries can intersect the same tile.
                    seen.add(item["id"])
                    if not shape(item["geometry"]).intersects(shape(aoi)):
                        continue
                    if params.get('acquisition_ids') and item['id'] not in params['acquisition_ids']:
                        continue
                    selected = [(key, value) for key, value in item["assets"].items() if key in product.semantics.get("asset_keys", []) or (product.semantics.get("asset_pattern") and re.search(product.semantics["asset_pattern"], key))]
                    if not selected:
                        raise ValueError("STAC tile has no asset matching the explicit product variant")
                    for key, value in selected:
                        entry = asset(value["href"], id=item["properties"].get("datetime", "")+":"+item["id"]+":"+key, metadata={"item": item["id"], "asset": value, "properties": item["properties"], "footprint": item["geometry"]})
                        checksum = value.get("checksum:multihash", "").lower()
                        if checksum.startswith("1220") and len(checksum) == 68:
                            entry.expected_hash = checksum[4:]
                        assets.append(transport.inspect(entry))
                following = [link["href"] for link in payload.get("links", []) if link.get("rel") == "next"]
                if len(following) > 1 or len(assets) > 2048:
                    raise ValueError("Invalid or oversized STAC tile inventory")
                url, query = (following[0] if following else None), None
        if params.get('acquisition_ids') and {a.metadata['item'] for a in assets}!=set(params['acquisition_ids']):
            raise ValueError('Requested acquisition IDs are absent from the complete intersecting STAC inventory; no alternate acquisition will be selected')
    elif adapter == 'hwsd':
        from .hwsd import discover as discover_hwsd
        return discover_hwsd(product,transport)
    elif adapter == 'os_terrain50':
        from .os_terrain import discover as discover_terrain
        return discover_terrain(product,aoi,transport)
    elif adapter == "os_downloads":
        metadata, receipt = transport.json(product.endpoint)
        snapshots.append(receipt)
        product.version = metadata["version"]
        downloads, receipt = transport.json(metadata["downloadsUrl"], allow_list=True)
        snapshots.append(receipt)
        selected = [x for x in downloads if x.get("format") == product.semantics["format"] and x.get("area") == "GB"]
        if len(selected) != 1:
            raise ValueError("Ordnance Survey download variant is missing or ambiguous")
        row = selected[0]
        entry = asset(row["url"], id=product.id+":"+product.version, size=row["size"], metadata=row)
        entry.filename = row["fileName"]
        entry.hash_algorithm, entry.expected_hash = "md5", row["md5"]
        assets.append(entry)
    elif adapter == "worldclim":
        url = f"{product.endpoint}/{params['country']}_wc2.1_30s_{product.semantics['variable']}.tif"
        try:
            assets.append(transport.inspect(asset(url)))
        except ValueError as exc:
            if 'HTTP 404' not in str(exc):
                raise
            url = f"https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_30s_{product.semantics['variable']}.zip"
            assets.append(transport.inspect(asset(url, metadata={'monthly_archive': True})))
            findings.append(Finding(id=product.id+':global-archive',rule='download-scope',severity='acknowledgement',message='The country mirror has no file for this variable. Review the full global archive size for the same WorldClim release before confirming.',basis='https://worldclim.org/data/worldclim21.html'))
    elif adapter == 'ghs_pop':
        name = f"GHS_POP_E{params['year']}_GLOBE_R2023A_54009_1000"
        assets.append(transport.inspect(asset(f'{product.endpoint}/{name}/V1-0/{name}_V1_0.zip',metadata={'reference_year':params['year'],'model_version':'R2023A V1-0'})))
        if int(params['year']) > 2020:
            findings.append(Finding(id=product.id+':extrapolated',rule='population-epoch',severity='acknowledgement',message='The selected future epoch is a modelled projection, not a census count for that year.',basis=product.assessment.documentation[0]))
    elif adapter == 'gsw':
        listing = transport.download(asset(product.endpoint,id='gsw-file-inventory',role='index'))
        snapshots.append({'receipt':listing.model_dump(mode='json')})
        names = set(transport.store.verify_blob(listing.sha256).read_text().splitlines())
        tiles = set()
        for w,s,e,n in boxes:
            tiles.update((lon,lat) for lon in range(math.floor(w/10)*10,math.ceil(e/10)*10,10) for lat in range(math.floor(s/10)*10+10,math.ceil(n/10)*10+1,10))
        for lon,lat in sorted(tiles):
            name = f"download2024/Aggregated/VER1-5/occurrence/occurrence_{abs(lon)}{'E' if lon>=0 else 'W'}_{abs(lat)}{'N' if lat>=0 else 'S'}_v1_5_2024.tif"
            if name not in names:
                findings.append(Finding(id=f'gsw:absent:{lon}:{lat}',rule='catalogue-coverage',severity='acknowledgement',message=f'No occurrence tile in the retained publisher inventory at {lon}, {lat}.',evidence={'bbox':[lon,lat-10,lon+10,lat]}))
                continue
            assets.append(transport.inspect(asset(urljoin(product.endpoint,name),metadata={'footprint':[lon,lat-10,lon+10,lat]})))
    elif adapter == 'stac_landcover':
        from .stac_landcover import discover as discover_landcover
        return discover_landcover(product,params,aoi,transport)
    elif adapter == "stac":
        body = {"collections": [product.semantics["collection"]], "intersects": aoi, "datetime": f"{params['start']}T00:00:00Z/{params['end']}T23:59:59Z", "limit": 100,
                "query": {"eo:cloud_cover": {"lte": params["max_cloud_cover"]}}}
        url, method, seen = product.endpoint+"/search", "POST", set()
        while url:
            value, receipt = transport.json(url, method=method, json_body=body if method == "POST" else None)
            snapshots.append(receipt)
            if value.get("type") != "FeatureCollection" or not isinstance(value.get("features"), list):
                raise ValueError("Invalid STAC search response")
            for item in value["features"]:
                key = item["id"]
                if key in seen:
                    raise ValueError("Duplicate STAC item across pages")
                seen.add(key)
                if len(seen) > 128:
                    raise ValueError("Imagery selection exceeds 128 scenes; shorten date range")
                if params.get('scene_ids') and key not in params['scene_ids']:
                    continue
                from .imagery import calibration
                if "product-metadata" not in item["assets"]:
                    raise ValueError("Scene lacks its mandatory calibration metadata")
                metadata_asset = asset(item["assets"]["product-metadata"]["href"], id=key+":product-metadata", role="metadata")
                metadata_receipt = transport.download(metadata_asset, actual_url=transport.signed_url(metadata_asset.url))
                snapshots.append({"receipt": metadata_receipt.model_dump(mode="json"), "role": "calibration", "scene": key})
                calibrations = calibration(transport.store.verify_blob(metadata_receipt.sha256).read_bytes(), item["properties"].get("s2:processing_baseline"), params["bands"])
                for band in sorted(set(params["bands"] + product.semantics.get("quality_assets", []))):
                    if band not in item["assets"]:
                        raise ValueError(f"Scene {key} is missing required asset {band}")
                    item_asset = item["assets"][band]
                    planned_asset = asset(item_asset["href"], id=f"{key}:{band}", role="quality" if band in product.semantics.get("quality_assets", []) else "data",
                                        metadata={"scene": key, "band": band, "properties": item["properties"], "asset": item_asset,
                                                  "calibration": calibrations.get(band), "calibration_sha256": metadata_receipt.sha256})
                    assets.append(transport.inspect(planned_asset, actual_url=transport.signed_url(planned_asset.url)))
            links = [x for x in value.get("links", []) if x.get("rel") == "next"]
            if len(links) > 1:
                raise ValueError("Ambiguous STAC pagination")
            if links:
                link = links[0]
                url, method = link["href"], link.get("method", "GET")
                body = link.get("body", body)
            else:
                url = None
        if params.get('scene_ids') and {a.metadata['scene'] for a in assets}!=set(params['scene_ids']):
            raise ValueError('Requested scene IDs are absent from the complete matching STAC inventory; no alternate scene will be selected')
    elif adapter == "worldpop":
        value, receipt = transport.json(product.endpoint, params={"iso3": params["country"].lower()})
        snapshots.append(receipt)
        matches = [x for x in value.get("data", []) if str(x.get("popyear")) == str(params["year"])]
        if len(matches) != 1:
            raise ValueError("WorldPop release is absent or ambiguous; no automatic product substitution")
        row = matches[0]
        urls = row.get("files", [])
        if not urls:
            raise ValueError("WorldPop release lacks downloadable assets")
        for url in urls:
            if url.lower().endswith(".tif"):
                assets.append(transport.inspect(asset(url, metadata=row)))
    elif adapter == "soilgrids":
        from .soilgrids import discover as discover_soilgrids
        return discover_soilgrids(product,params,aoi,transport)
    elif adapter == "cds":
        from datetime import date
        from .climate_acquisition import cds_requests
        if date.fromisoformat(params['end'])>=datetime.fromisoformat(as_of.replace('Z','+00:00')).date():
            raise ValueError('An hourly ERA5 selection must end before the reference UTC day so all 24 requested times have elapsed')
        inventory=cds_requests(product,params,boxes)
        snapshots.append({"cds": {"dataset": product.semantics["dataset"], "parameters": params, "bbox": boxes, "as_of": as_of,'requests':inventory}})
        product.semantics['request_partition']='One provider response per UTC day and variable; no implicit combination of accumulation and instantaneous products'
        findings.append(Finding(id="cds:server-processing", rule="provider-derived-response", severity="acknowledgement", message="CDS generates this requested subset server-side. ZEUS retains the exact returned bytes; independent replay starts from that response.", basis="https://cds.climate.copernicus.eu/how-to-api"))
    elif adapter == 'eccc_catalogue':
        value, receipt = transport.json(product.endpoint, params={'path':product.semantics['catalogue_path']})
        snapshots.append(receipt)
        matches = [row for row in value.get('path_contents',[]) if not row.get('is_directory') and row.get('name')==product.semantics['filename']]
        if len(matches)!=1:
            raise ValueError('The published ECCC catalogue does not contain the exact selected release')
        row=matches[0]
        if row['path'] != product.semantics['catalogue_path'].lstrip('/')+'/'+row['name']:
            raise ValueError('ECCC catalogue asset path contradicts the selected release')
        # Resolve the official file API's ephemeral authorization only in memory.
        from urllib.parse import quote
        entry=asset('https://data-donnees.az.ec.gc.ca/public//'+quote(row['path'],safe='/'),id=row['name'],metadata=row)
        entry.filename=row['name']
        assets.append(transport.inspect(entry, actual_url=transport.signed_url(entry.url)))
    elif adapter in ("http", "ogc_features"):
        if adapter == "http":
            assets.append(transport.inspect(asset(product.endpoint, metadata=product.semantics)))
        else:
            snapshots.append({"ogc_features": {"endpoint": product.endpoint, "boxes": boxes}})
    elif adapter == "nhn":
        from .national import discover_nhn
        return discover_nhn(product, aoi, transport)
    else:
        raise ValueError(f"No verified adapter implementation for {adapter}")
    return assets, snapshots, findings
