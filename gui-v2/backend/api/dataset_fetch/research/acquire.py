from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .contracts import AcquisitionReceipt, Asset, canonical, digest
from .store import atomic_json


def acquire(selection, transport, checkpoint, save_checkpoint):
    """Receipt is persisted after each fully verified object. Resume never trusts size alone."""
    result = []
    adapter = selection.product.adapter
    for entry in selection.discovery:
        if "receipt" in entry:
            receipt = AcquisitionReceipt.model_validate(entry["receipt"])
            transport.store.verify_blob(receipt.sha256)
            result.append(receipt)
    if adapter=='cds':
        from .climate_acquisition import acquire_cds
        return result+acquire_cds(selection,transport,checkpoint,save_checkpoint)
    if adapter in ("overpass", "ogc_features"):
        key = "response:"+selection.id
        previous = checkpoint.get(key)
        if previous:
            receipts = [AcquisitionReceipt.model_validate(x) for x in previous]
            for r in receipts:
                transport.store.verify_blob(r.sha256)
            return result + receipts
        if adapter == "overpass":
            descriptor = next(d['overpass'] for d in selection.discovery if 'overpass' in d)
            if not descriptor['expected_roots']:
                receipts=[]  # The retained discovery receipt proves the empty root inventory.
            else:
                payload, entry = transport.json(selection.product.endpoint, method="POST", data={"data": descriptor['query']})
                if not isinstance(payload.get("elements"), list) or not payload.get("osm3s", {}).get("timestamp_osm_base"):
                    raise ValueError("Overpass response lacks a complete typed extract or base timestamp")
                osm_features(payload,descriptor['expected_roots'])
                receipts = [AcquisitionReceipt.model_validate(entry["receipt"]).model_copy(update={'asset_id':'overpass-response:'+selection.id})]
        elif adapter == "ogc_features":
            receipts = []
            from urllib.parse import urljoin
            seen_ids = {}
            for bbox in next(d["ogc_features"]["boxes"] for d in selection.discovery if "ogc_features" in d):
                url, params = selection.product.endpoint, {"bbox": ",".join(map(str, bbox)), "limit": 1000, "f": "json"}
                visited, query_ids, expected = set(), set(), None
                while url:
                    marker = (url, canonical(params))
                    if marker in visited:
                        raise ValueError("OGC pagination loop")
                    visited.add(marker)
                    payload, entry = transport.json(url, params=params)
                    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
                        raise ValueError("OGC response is not a FeatureCollection")
                    matched = payload.get('numberMatched')
                    if isinstance(matched, bool) or not isinstance(matched, int) or matched < 0:
                        raise ValueError('OGC source does not expose a quantifiable result inventory')
                    if expected is not None and expected != matched:
                        raise ValueError('OGC inventory changed during pagination')
                    expected = matched
                    if expected > 1_000_000 or payload.get('numberReturned', len(payload['features'])) != len(payload['features']):
                        raise ValueError('OGC result is oversized or has a contradictory returned count')
                    for feature in payload["features"]:
                        feature_key = str(feature.get('id'))
                        if feature.get('id') is None or feature_key in query_ids:
                            raise ValueError("OGC feature identities are missing or duplicated")
                        query_ids.add(feature_key)
                        value = digest(feature)
                        if feature_key in seen_ids and seen_ids[feature_key] != value:
                            raise ValueError('OGC source changed between AOI queries')
                        seen_ids[feature_key] = value
                    receipts.append(AcquisitionReceipt.model_validate(entry["receipt"]).model_copy(update={'asset_id':f'ogc-page:{len(receipts):06d}'}))
                    following = [x["href"] for x in payload.get("links", []) if x.get("rel") == "next"]
                    if len(following) > 1:
                        raise ValueError("Ambiguous OGC pagination")
                    url, params = (urljoin(url, following[0]) if following else None), None
                if len(query_ids) != expected:
                    raise ValueError('OGC response ended before all matched IDs were acquired')
        checkpoint[key] = [r.model_dump(mode="json") for r in receipts]
        save_checkpoint(checkpoint)
        return result + receipts
    for asset in selection.assets:
        transport.check()
        key = selection.id+":"+asset.id
        if key in checkpoint:
            receipt = AcquisitionReceipt.model_validate(checkpoint[key])
            transport.store.verify_blob(receipt.sha256)
        elif adapter == "arcgis":
            query = asset.metadata['request']
            payload, entry = transport.json(asset.url, method="POST", data=query)
            if payload.get("type") != "FeatureCollection" or payload.get("exceededTransferLimit") or not isinstance(payload.get("features"), list):
                raise ValueError("ArcGIS returned an incomplete feature batch")
            field = asset.metadata["object_id_field"]
            ids = [f.get("properties", {}).get(field, f.get("id")) for f in payload["features"]]
            if len(ids) != len(set(ids)) or set(ids) != set(asset.metadata["ids"]):
                raise ValueError("ArcGIS acquired IDs do not match the frozen inventory")
            from .arcgis_contract import validate_features
            validate_features(payload,next(d['arcgis']['metadata'] for d in selection.discovery if 'arcgis' in d))
            receipt = AcquisitionReceipt.model_validate(entry["receipt"]).model_copy(update={"asset_id": asset.id})
        else:
            actual_url = None
            if adapter == 'eccc_catalogue' or (adapter in ('stac','stac_landcover') and "blob.core.windows.net" in asset.url):
                actual_url = transport.signed_url(asset.url)
            receipt = transport.download(asset, actual_url=actual_url)
        checkpoint[key] = receipt.model_dump(mode="json")
        save_checkpoint(checkpoint)
        result.append(receipt)
    if adapter == "arcgis":
        frozen = next(d["arcgis"] for d in selection.discovery if "arcgis" in d)
        if not frozen.get("historicMoment") and frozen.get("editingInfo"):
            current, metadata_receipt = transport.json(selection.product.endpoint, params={"f": "json"})
            result.append(AcquisitionReceipt.model_validate(metadata_receipt['receipt']))
            if current.get("editingInfo") != frozen["editingInfo"]:
                raise ValueError("ArcGIS layer changed while acquiring batches; create a new plan")
    return result


def materialize(selection, receipts, store, directory):
    directory.mkdir(parents=True, exist_ok=True)
    by_id = {r.asset_id: r for r in receipts}
    paths = []
    for index, asset in enumerate(selection.assets):
        if asset.id not in by_id:
            raise ValueError("Missing mandatory asset receipt")
        receipt = by_id[asset.id]
        blob = store.verify_blob(receipt.sha256)
        suffix = Path(asset.filename).suffix or ".bin"
        from .national import check_signature
        with blob.open('rb') as source:
            check_signature(asset.filename, source.read(16))
        target = directory / f"{index:05d}{suffix}"
        if target.exists():
            target.unlink()
        try:
            os.link(blob, target)
        except OSError:
            shutil.copyfile(blob, target)
        paths.append((asset, str(target)))
    return paths


from .osm import osm_features
