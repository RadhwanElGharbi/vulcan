"""Assess every historical catalogue row without inventing source capabilities.

Retains official endpoint/document responses as evidence. Reachability is not
scientific qualification: untested requirements explicitly remain not_evaluated.
"""
import csv
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from api.dataset_fetch.research.contracts import canonical, digest, now
from api.dataset_fetch.research.registry import registry
from api.dataset_fetch.research.store import Store, atomic_json
from api.dataset_fetch.research.transport import Transport

RULES = ["source_identity", "version_identity", "observation_period", "native_resolution", "accuracy", "units", "crs", "license", "acquisition_completeness", "snapshot_replay", "live_acquisition"]


def category(row):
    text = (row["Type"]+" "+row["Dataset"]).lower()
    if any(term in text for term in ('radon', 'quick clay', 'kvikkleire', 'unstable rock', 'permafrost')): return 'geohazard'
    if 'sea ice' in text: return 'climate'
    if any(term in text for term in ('american indian', 'native hawaiian', 'reindeer husbandry')): return 'indigenous_lands'
    if 'cropland' in text: return 'landcover'
    for cat, patterns in [("imagery", ["imagery", "orthophoto", "multispectral"]), ("climate", ["climate", "weather", "meteorolog"]), ("population", ["population", "demographic", "census"]),
                          ("protected_areas", ["protected", "conserved"]), ("indigenous_lands", ["indigenous", "aboriginal"]), ("dem", ["elevation", " dtm", " dsm", "dem", "terrain", "lidar"]),
                          ("landcover", ["land cover", "land use", "worldcover"]), ("soil", ["soil", "geology"]), ("geohazard", ["hazard", "landslide", "seismic", "flood"]),
                          ("railways", ["rail"]), ("roads", ["road"]), ("powerlines", ["power", "utilit", "transmission"]), ("pipelines", ["pipeline"]), ("waterways", ["hydro", "water"])]:
        if any(p in text for p in patterns): return cat
    return None


def match(row, cat, products):
    text = row["Dataset"].lower()
    if text.strip()=='fao harmonized world soil database (hwsd) v2.0':return ['fao-hwsd2-native']
    if text.strip()=='esri sentinel-2 land use/land cover (10m global)':return ['io-lulc-2020-10class','io-lulc-annual-v1','io-lulc-annual-v2']
    if text.strip()=='esri 2020 global lulc from sentinel-2':return ['io-lulc-2020-10class']
    if text.strip()=='srtm version 4.1': return ['cgiar-srtm-4.1']
    if "copernicus" in text and "dem" in text and "10" not in text:
        if 'glo-90' in text: return ['copernicus-glo90']
        return ["copernicus-glo30"]
    if "worldcover" in text or "world cover" in text:
        if ('2021' in text or 'v200' in text) and '2020' not in text: return ['worldcover-2021']
        if ('2020' in text or 'v100' in text) and '2021' not in text: return ['worldcover-2020']
        return ["worldcover-2020", "worldcover-2021"]
    if "soilgrids" in text: return sorted(k for k in products if k.startswith('soilgrids-'))
    if "3dep" in text: return ["usgs-3dep-1m", "usgs-3dep-10m", "usgs-3dep-30m"]
    if ("sentinel-2" in text or "sentinel 2" in text) and cat == 'imagery': return ["sentinel2-l2a"]
    if "era5" in text: return ["era5-single-levels"]
    if "worldpop" in text and 'density' not in text: return ["worldpop-counts"]
    if "worldclim" in text: return sorted(k for k in products if k.startswith("worldclim-"))
    if 'hydrorivers' in text: return ['hydrorivers-v1']
    if 'hydrolakes' in text: return ['hydrolakes-v1']
    if 'ghs-pop' in text and '2023' in text: return ['ghs-pop-2023a-1km']
    if ('global surface water' in text or 'gsw' in text) and '1.4' not in text: return ['jrc-gsw-occurrence-1.5']
    if "swissalti3d" in text: return ["swissalti3d-2m"]
    if row['ISO3']=='USA' and 'north american rail network' in text:
        return ['fra-narn-lines','fra-narn-nodes'] if 'nodes' in text or text.strip()=='north american rail network' else ['fra-narn-lines']
    if row['ISO3']=='USA' and text.strip()=='american indian, alaska native, native hawaiian areas': return ['census-aiannha-2026']
    if row["ISO3"] == "GBR":
        if text.strip()=='os terrain 50':return ['os-terrain50']
        if text.strip()=='natural england protected areas': return ['eng-nnr','eng-lnr']
        if "os open roads" in text: return ["os-openroads"]
        if "os open rivers" in text: return ["os-openrivers"]
    if row['ISO3']=='NOR' and text.strip()=='national protected areas database':return ['nor-protected-areas']
    if any(t in text for t in ["openstreetmap", "osm ", "openinframap"]):
        key = "osm-"+str(cat)
        return [key] if key in products else []
    if row["ISO3"] == "CAN":
        if "hrdem" in text: return ["can-hrdem-dtm","can-hrdem-dsm"]
        if text.strip() == 'indigenous lands' and 'clss.nrcan' in row.get('URL',''): return ["can-clss"]
        if "nhn" in text or "national hydro" in text: return ["can-nhn"]
        if text.strip() == 'national protected areas database': return ["can-cpcad"]
        if "federal pipeline" in text: return ["can-cer"]
    return []


def fetch_evidence(url):
    store = Store()
    try:
        with requests.get(url, timeout=(8,12), stream=True, headers={"User-Agent":"ZEUS-SourceAudit/2.0"}) as response:
            content = bytearray()
            for block in response.iter_content(65536):
                content.extend(block)
                if len(content) > 4*1024**2: break
            receipt = Transport(store).retain_bytes(bytes(content), url, response.headers)
            return {"url": url, "checked_at": now(), "http_status": response.status_code, "sha256": receipt.sha256, "retained_bytes": len(content),
                    "truncated_evidence": len(content) > 4*1024**2, "status": "retrieved" if response.ok else "unavailable", "final_url": response.url}
    except Exception as exc:
        return {"url": url, "checked_at": now(), "status": "unavailable", "error_type": type(exc).__name__}


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--reuse-evidence",action="store_true")
    args=parser.parse_args()
    Store()  # Initialize the ledger before concurrent evidence reads.
    rows = list(csv.DictReader((ROOT/"docs/datasets/WORLD_DATASET_CATALOGUE.csv").open(encoding="utf-8-sig")))
    products = registry()
    entries, urls = [], set()
    for number, row in enumerate(rows, 1):
        cat = category(row)
        ids = match(row, cat, products)
        access = row.get("Access", "").lower()
        restricted = any(s in access for s in ("commercial/purchase", "commercial purchase", "commercial license required", "commercial -", "subscription", "restricted", "formal request", "requires request", "requires approval"))
        mixed = restricted and any(s in access for s in ('open', 'free', 'public', 'mixed')) and 'not publicly' not in access
        paid = restricted and not mixed
        outside = cat is None and any(s in (row['Type']+' '+row['Dataset']).lower() for s in ('administrative boundar', 'official boundar', 'archaeolog', 'heritage', 'military', 'conflict', 'mineral concession'))
        url = re.split(r"\[\d", row.get("URL", ""))[0].strip().rstrip(".,;")
        if url.startswith("http://"): url = "https://"+url[7:]
        if not url.startswith("https://") or not urlsplit(url).hostname: url = None
        docs = sorted({url} if url else set())
        for pid in ids: docs.extend(products[pid].assessment.documentation)
        docs = sorted(set(docs))
        protected_planet=('wdpa' in row['Dataset'].lower() or 'world database on protected areas' in row['Dataset'].lower())
        if protected_planet:
            docs=sorted(set(docs+['https://api.protectedplanet.net/documentation','https://api.protectedplanet.net/request','https://www.protectedplanet.net/en/legal']))
        urls.update(docs)
        disposition = "requires_acknowledgement" if ids else "excluded" if paid or outside else "unavailable"
        reasons = []
        if not ids:
            reasons.append("Catalogue describes paid/individually restricted access; verify current official terms" if paid else "Outside the selected acquisition categories" if outside else "Mixed access or ambiguous scope requires product-level assessment" if mixed or cat is None else "Product and adapter qualification outstanding; not excluded from implementation scope")
        if not docs: reasons.append("No usable official evidence URL in historical catalogue")
        requirements = {rule: {"status": "not_evaluated", "evidence": []} for rule in RULES}
        if protected_planet:
            requirements['source_identity']={'status':'provider_documented_product_release_pending','evidence':['https://api.protectedplanet.net/documentation'],
                'findings':'UNEP-WCMC Protected Planet API v4; v3 deprecated. API version is not dataset release identity.','reviewed_at':'2026-09-17'}
            requirements['license']={'status':'provider_conditions_documented_eligibility_pending','evidence':['https://www.protectedplanet.net/en/legal','https://api.protectedplanet.net/request'],
                'findings':'Provider approval required for API credentials; commercial use and redistribution restricted. Bulk download interfaces need separate eligibility assessment.','reviewed_at':'2026-09-17'}
            requirements['acquisition_completeness'].update({'evidence':['https://api.protectedplanet.net/documentation'],
                'findings':'Maximum page size 50; actual complete ID/count inventory, field permissions and snapshot behavior remain unverified. Documentation example counts are inconsistent.'})
            reasons.append('API requires individual approval. Bulk downloads remain to be assessed; do not blanket-exclude this product because of the API restriction. See protected-planet-assessment.md.')
        for pid in ids:
            p = products[pid]
            for rule, value in {'source_identity':{'id':p.id,'publisher':p.publisher}, 'units':p.units, 'native_resolution':p.native_spacing, 'accuracy':p.accuracy, 'observation_period':p.observation_period, 'license':p.license}.items():
                if value is None: continue
                requirements[rule]['status']='documented_product_fields_pending_full_qualification'
                requirements[rule]['evidence']=sorted(set(requirements[rule]['evidence']+p.assessment.documentation))
                requirements[rule].setdefault('products',{})[pid]=value
            requirements["snapshot_replay"] = {"status": "implemented_not_live_verified", "evidence": ["research transport and offline replay"]}
        entries.append({"row": number, "row_hash": digest(row), "country": row["ISO3"], "dataset": row["Dataset"], "category": cat, "product_ids": ids,
                        "disposition": disposition, "assessment_status": "pending_live_qualification" if ids else "pending_source_qualification" if disposition == "unavailable" else "catalogue_access_or_scope_review",
                        "missing_contract": [rule for rule, result in requirements.items() if result["status"] == "not_evaluated"], "requirements": requirements, "reasons": reasons, "documentation": docs, "original": row})
    evidence = {}
    previous=ROOT/"docs/datasets/catalogue-assessment.json"
    if args.reuse_evidence and previous.exists():
        evidence=json.loads(previous.read_text(encoding="utf-8"))["evidence"]
    with ThreadPoolExecutor(max_workers=6) as executor:
        jobs = {executor.submit(fetch_evidence,url): url for url in sorted(urls) if url not in evidence}
        for i, future in enumerate(as_completed(jobs),1):
            evidence[jobs[future]] = future.result()
            if i % 25 == 0: print(f"Evidence endpoints examined: {i}/{len(jobs)}", flush=True)
    result = {"schema_version": "zeus.catalogue-assessment/1.0", "generated_at": now(), "catalogue_rows": len(rows), "catalogue_sha256": hashlib.sha256((ROOT/"docs/datasets/WORLD_DATASET_CATALOGUE.csv").read_bytes()).hexdigest(),
              "assessment_complete": False, "qualification_note": "An HTTP response is documentary evidence, not scientific qualification. Unevaluated contract requirements and missing adapters remain outstanding.",
              "counts": {status: sum(e["disposition"] == status for e in entries) for status in ("qualified","requires_acknowledgement","unavailable","excluded")}, "entries": entries, "evidence": evidence}
    atomic_json(ROOT/"docs/datasets/catalogue-assessment.json", result)
    print(json.dumps({"rows":len(rows),"endpoints":len(evidence),"counts":result["counts"]}), flush=True)


if __name__ == "__main__": main()
