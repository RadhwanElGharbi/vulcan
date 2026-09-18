"""Live discovery checks, separate from fixture tests and acquisition certification."""
import concurrent.futures
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"gui-v2/backend"))
from api.dataset_fetch.research.discovery import discover
from api.dataset_fetch.research.registry import registry,parameters
from api.dataset_fetch.research.store import Store,atomic_json
from api.dataset_fetch.research.transport import Transport
from api.dataset_fetch.research.contracts import now


def probe(item):
    w,s=(-105.27,40.01) if item.id.startswith("usgs") else (-80.55,43.47)
    if item.id.startswith("swiss"): w,s=7.43,46.94
    if item.id.startswith("os-"): w,s=-0.13,51.50
    if item.id=='nor-protected-areas':w,s=10.383,59.783
    if item.id=='nor-proposed-protected-areas':w,s=24.197,69.730
    if item.id=='eng-nnr':w,s=1.685,52.730
    if item.id=='eng-lnr':w,s=-2.182,53.866
    aoi={"type":"Polygon","coordinates":[[[w,s],[w+.005,s],[w+.005,s+.005],[w,s+.005],[w,s]]]}
    values={}
    if item.adapter == "stac": values={"start":"2026-09-01","end":"2026-09-03"}
    if item.adapter == "worldpop": values={"country":"CAN","year":2020}
    if item.adapter == "ghs_pop": values={'year':'2020'}
    if item.adapter == "worldclim": values={"country":"ARE"}; w,s=54.37,24.45
    aoi={"type":"Polygon","coordinates":[[[w,s],[w+.005,s],[w+.005,s+.005],[w,s+.005],[w,s]]]}
    if item.adapter == "cds": return {"product":item.id,"status":"credentials_required" if not os.getenv("CDSAPI_KEY") else "not_executed","stage":"discovery","checked_at":now()}
    try:
        assets,snapshots,findings=discover(item,parameters(item,values),aoi,now(),Transport(Store()))
        return {"product":item.id,"status":"passed" if assets or item.adapter in ("overpass","arcgis") else "empty_inventory", "stage":"discovery", "checked_at":now(),"assets":len(assets),"known_bytes":sum(a.size or 0 for a in assets),"snapshot_receipts":snapshots,"findings":[f.model_dump(mode="json") for f in findings]}
    except Exception as exc:
        return {"product":item.id,"status":"failed","stage":"discovery","checked_at":now(),"error":str(exc)}


if __name__ == "__main__":
    Store()
    previous_path=ROOT/"qa/research-provider-discovery.json"
    result=[]
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--products",nargs="*")
    args=parser.parse_args()
    if args.products and previous_path.exists():
        result=[p for p in json.loads(previous_path.read_text())['products'] if p['product'] not in args.products]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs=[pool.submit(probe,p) for p in registry().values() if not args.products or p.id in args.products]
        for future in concurrent.futures.as_completed(jobs):
            entry=future.result(); result.append(entry)
            atomic_json(ROOT/"qa/research-provider-discovery.json",{"checked_at":now(),"scope":"Discovery only; not end-to-end acquisition qualification", "products":sorted(result,key=lambda r:r["product"])})
            print(json.dumps({k:v for k,v in entry.items() if k not in ("snapshot_receipts","findings")}),flush=True)
    atomic_json(ROOT/"qa/research-provider-discovery.json",{"checked_at":now(),"scope":"Discovery only; not end-to-end acquisition qualification", "products":sorted(result,key=lambda r:r["product"])})
