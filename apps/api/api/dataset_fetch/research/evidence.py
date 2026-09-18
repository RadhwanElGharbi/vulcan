"""Attach preserved documentation to plans without inventing qualification results."""
from __future__ import annotations

import json
import requests
from .contracts import AcquisitionReceipt, Finding, digest
from .store import ROOT, Store
from .transport import ProviderResponseError


def documentary_evidence(product, transport):
    path = ROOT / 'docs/datasets/catalogue-assessment.json'
    evidence = json.loads(path.read_text(encoding='utf-8')).get('evidence', {}) if path.exists() else {}
    source_store = Store()
    snapshots, missing = [], []
    for url in product.assessment.documentation:
        record = evidence.get(url, {})
        if record.get('status') != 'retrieved' or record.get('truncated_evidence') or not record.get('sha256'):
            # Expanded products need documentation even before a catalogue row
            # has been linked to them. Network failures are reviewable; failure
            # to preserve fetched evidence must propagate and stop planning.
            content=bytearray();headers={};problem=None
            try:
                with transport.request('GET',url,stream=True,deadline=45) as response:
                    headers=response.headers
                    for block in transport.chunks(response):
                        if len(content)+len(block)>4*1024**2:
                            problem='Document exceeds the 4 MiB retained-evidence limit'
                            break
                        content.extend(block)
            except (requests.RequestException,ProviderResponseError) as exc:
                problem=str(exc)
            if problem:
                missing.append({'url':url,'reason':problem})
                continue
            receipt=transport.retain_bytes(bytes(content),url,headers).model_copy(update={'asset_id':'documentation:'+digest(url)})
            snapshots.append({'documentation':{'url':url,'retrieved_at':receipt.acquired_at,'status':'retained_not_qualified'},'receipt':receipt.model_dump(mode='json')})
            continue
        source = source_store.verify_blob(record['sha256'])
        # Documentation responses are bounded to 4 MiB by the catalogue audit.
        receipt = transport.retain_bytes(source.read_bytes(), url)
        receipt = receipt.model_copy(update={'asset_id':'documentation:'+digest(url), 'acquired_at':record['checked_at']})
        snapshots.append({'documentation':{'url':url,'retrieved_at':record['checked_at'],'status':'retained_not_qualified'}, 'receipt':receipt.model_dump(mode='json')})
    findings = []
    if missing:
        findings.append(Finding(id=product.id+':documentation-pending', rule='retained-documentation', severity='acknowledgement',
                                message='Some provider documentation could not be retained. The source assessment remains incomplete.', evidence={'urls':missing}))
    return snapshots, findings
