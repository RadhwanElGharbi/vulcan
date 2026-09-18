"""Dated live qualification, isolated from user projects. No result is research approval."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]


def run(product,root):
    w,s,epsg=-80.55,43.47,32617
    params={}
    if product.startswith('usgs-'): w,s,epsg=-105.27,40.01,32613
    if product=='can-cpcad': w,s,epsg=-116.218,51.417,32611
    if product.startswith('fra-narn-'): w,s,epsg=-87.642,41.876,32616
    if product=='census-aiannha-2026': w,s,epsg=-111.75,33.50,32612
    if product=='cgiar-srtm-4.1': w,s,epsg=54.37,24.45,32640
    if product.startswith('can-hrdem-'): w,s,epsg=-106.67,52.13,32613;params={'acquisition_ids':['SK-Saskatoon_2019-1m']}
    if product.startswith('swiss'): w,s,epsg=7.433,46.943,2056
    if product.startswith('os-'): w,s,epsg=-.13,51.50,27700
    if product=='nor-protected-areas':w,s,epsg=10.383,59.783,25833
    if product=='nor-proposed-protected-areas':w,s,epsg=24.197,69.730,25833
    if product=='eng-nnr':w,s,epsg=1.685,52.730,27700
    if product=='eng-lnr':w,s,epsg=-2.182,53.866,27700
    if product.startswith('worldclim'): w,s,epsg=54.37,24.45,32640; params={'country':'ARE'}
    if product=='sentinel2-l2a': params={'start':'2026-09-01','end':'2026-09-03','bands':['B04']}
    if product=='worldpop-counts': w,s,epsg=103.85,1.28,32648; params={'country':'SGP','year':2020}
    if product=='ghs-pop-2023a-1km': params={'year':'2020'}
    aoi={'type':'Polygon','coordinates':[[[w,s],[w+.003,s],[w+.003,s+.003],[w,s+.003],[w,s]]]}
    project=root/product; project.mkdir(parents=True,exist_ok=True)
    ctx=SimpleNamespace(project_path=project,target_epsg=epsg)
    planning.context=lambda name:(ctx,aoi)
    worker.context=planning.context
    store=Store(project/'store')
    if args.reuse_store:
        from api.dataset_fetch.research.contracts import AcquisitionReceipt
        for location in args.reuse_store:
            previous=Store(Path(location))
            with previous.connect() as connection:
                jobs=[json.loads(row[0]) for row in connection.execute('SELECT payload FROM jobs')]
                records=[json.loads(row[0]) for row in connection.execute('SELECT receipt FROM object_cache')]
            records.extend(raw for job in jobs for group in job.get('receipts',{}).values() for raw in group)
            imported=set()
            for raw in records:
                receipt=AcquisitionReceipt.model_validate(raw)
                source=previous.verify_blob(receipt.sha256)
                if receipt.sha256 not in imported:
                    target=store.root/('import-'+receipt.sha256)
                    if not target.exists(): os.link(source,target)
                    store.retain(target);imported.add(receipt.sha256)
                store.cache_receipt(receipt)
    entry={'product':product,'checked_at':now(),'scope':'Isolated QA acquisition, publication and two offline replays; not approval for research use','input_cache_seed':args.reuse_store}
    job=None
    try:
        plan=planning.build_plan(product,PlanRequest(selections=[Selection(product_id=product,parameters=params)]),store)
        print(json.dumps({'product':product,'stage':'plan_frozen','assets':sum(len(s.assets) for s in plan.selections),'download_bytes':plan.estimates['known_download_bytes']}),flush=True)
        request=ExecuteRequest(plan_id=plan.plan_id,plan_hash=plan.plan_hash,idempotency_key='isolated-live-qualification',acknowledged_findings=[f.id for s in plan.selections for f in s.findings if f.severity=='acknowledgement'])
        job=store.create_job(worker.new_job(plan,request),request.model_dump(mode='json'))
        worker.execute(store,job['id'])
        job=store.job(job['id'])
        if job['status']=='awaiting_approval':
            report=job['report']
            approvals=job['approvals']+[{'kind':'report','hash':report['report_hash'],'finding_ids':[f['id'] for f in report['findings'] if f['severity']=='acknowledgement'],'actor':'isolated automated QA; not user research approval','at':now()}]
            store.update(job['id'],{'status':'publishing','approvals':approvals})
            worker.publish(store,job['id'])
        job=store.job(job['id'])
        first=replay(job['id'],project/'replay-one',store)
        # Shuffle physical receipt order to ensure source precedence is frozen in the plan.
        store.update(job['id'],{'receipts':{sid:list(reversed(rows)) for sid,rows in job['receipts'].items()}},event='qa_shuffle_receipts')
        second=replay(job['id'],project/'replay-two',store)
        job=store.job(job['id'])
        entry.update({'status':'passed','plan_hash':plan.plan_hash,'job_id':job['id'],'runtime':plan.runtime,'report':job['report'],
                      'replay_one':{k:v for k,v in first.items() if k!='report'},'replay_two':{k:v for k,v in second.items() if k!='report'},'receipts':job['receipts']})
        bundle=project/'provenance.zip'
        write_bundle(bundle,plan.model_dump(mode='json'),job,{'validation':job['report'],'events':store.events(job['id'])},store,project/'data/generations'/job['id'])
        entry['provenance_bundle']=str(bundle.relative_to(ROOT))
    except Exception as exc:
        if job is not None and store.job(job['id'])['status'] not in worker.TERMINAL:
            worker.fail_job(store,job['id'],exc)
        entry.update({'status':'failed','error':str(exc),'error_type':type(exc).__name__})
    atomic_json(project/'qualification.json',entry)
    print(json.dumps({k:v for k,v in entry.items() if k not in ('runtime','report','receipts')}),flush=True)
    return entry


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--products',nargs='+',default=['copernicus-glo30','soilgrids-soc','worldclim-tavg','swissalti3d-2m','osm-roads','can-clss','sentinel2-l2a'])
    parser.add_argument('--reuse-store',action='append',default=[],help='Seed isolated QA cache from verified retained inputs; fresh provider HEAD checks remain mandatory')
    args=parser.parse_args()
    root=ROOT/'.runtime/qualification'/datetime.now(timezone.utc).isoformat().replace('+00:00','Z').replace(':','').replace('.','')
    # A long provider transfer must keep testing the exact implementation that
    # started it, even while the main checkout continues to evolve.
    snapshot=root/'code'
    package=snapshot/'api/dataset_fetch/research';package.mkdir(parents=True)
    backend=ROOT/'gui-v2/backend'
    for source in (backend/'api/dataset_fetch/research').glob('*.py'): shutil.copyfile(source,package/source.name)
    for relative in ('api','api/dataset_fetch'):
        (snapshot/relative/'__init__.py').write_text('__path__.append('+repr(str(backend/relative))+')\n',encoding='utf-8')
    sys.path[:0]=[str(snapshot),str(backend)]
    os.environ['PYTHONPATH']=os.pathsep.join([str(snapshot),str(backend)])
    from api.dataset_fetch.research import planning,worker
    from api.dataset_fetch.research.contracts import PlanRequest,Selection,ExecuteRequest,now
    from api.dataset_fetch.research.store import Store,atomic_json
    from api.dataset_fetch.research.replay import replay
    from api.dataset_fetch.research.bundle import write_bundle
    report_path=ROOT/'qa/research-live-acquisition.json'
    for product in args.products:
        record=run(product,root)
        records=[r for r in json.loads(report_path.read_text())['results'] if r['product'] != product] if report_path.exists() else []
        records.append(record)
        atomic_json(ROOT/'qa/research-live-acquisition.json',{'checked_at':now(),'results':records})
