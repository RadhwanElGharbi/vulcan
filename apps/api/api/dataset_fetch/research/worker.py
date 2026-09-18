from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .acquire import acquire
from .contracts import AcquisitionReceipt, FetchPlan, Finding, canonical, digest, file_hash, now, validate_acknowledgements
from .planning import context, runtime_fingerprint
from .processing import run_selection
from .store import Store, atomic_json, durable_replace
from .transport import Cancelled, Transport

STAGES = ["prefetch_scan", "fetch", "raw_metadata", "process", "validation", "processed_metadata", "layer_publish"]
TERMINAL = {"succeeded", "failed", "cancelled"}


def new_job(plan, request):
    return {"id": uuid.uuid4().hex, "plan_id": plan.plan_id, "plan_hash": plan.plan_hash, "project": plan.project, "status": "pending", "progress": 0,
            "updated_at": now(), "started_at": None, "completed_at": None, "error": None, "current_category": None,
            "categories": {s.id: {"status": "queued", "label": s.product.name, "category": s.product.category, "stages": {stage: {"status": "queued"} for stage in STAGES}} for s in plan.selections},
            "logs": ["Confirmed immutable acquisition plan"], "layers": {}, "force": False, "checkpoints": {}, "receipts": {}, "approvals": [{"kind": "plan", "hash": plan.plan_hash, "finding_ids": request.acknowledged_findings, "at": now()}]}


def stage(store, job_id, sid, name, status, message=None):
    job = store.job(job_id)
    categories = job["categories"]
    categories[sid]["stages"][name] = {"status": status, "message": message, "updated_at": now()}
    categories[sid]["status"] = "running" if status == "running" else categories[sid]["status"]
    finished = sum(v["status"] == "succeeded" for c in categories.values() for v in c["stages"].values())
    logs = job["logs"] + ([message] if message else [])
    store.update(job_id, {"categories": categories, "logs": logs[-200:], "progress": finished/(len(categories)*len(STAGES)), "current_category": categories[sid]["category"]}, event="stage", expected={"running", "cancelling"})


def validate_frozen_context(plan):
    current_files={p.name:file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    if current_files != plan.runtime.get("implementation_files",current_files):
        raise ValueError("Implementation changed after confirmation; restart the server and create a new plan")
    ctx, aoi = context(plan.project)
    if digest(aoi) != plan.aoi_hash or f"EPSG:{ctx.target_epsg}" != plan.target_crs:
        raise ValueError("Project AOI or CRS changed after confirmation")
    for selection in plan.selections:
        original = selection.recipe.get('aoi_normalization')
        if original is not None and original != getattr(ctx, 'aoi_provenance', None):
            raise ValueError('Project AOI source inputs or normalization operation changed after confirmation')
    if runtime_fingerprint() != plan.runtime:
        raise ValueError("Runtime or implementation changed after confirmation; create a new plan")
    return ctx


def execute(store, job_id):
    job = store.job(job_id)
    if reconcile_committed(store, job):
        return
    if job["status"] == "publishing":
        publish(store, job_id)
        return
    if job["status"] == "cancelling":
        store.update(job_id, {"status": "cancelled", "completed_at": now()}, event="cancelled")
        return
    plan = FetchPlan.model_validate(store.plan(job["plan_id"]))
    ctx = validate_frozen_context(plan)
    needed = plan.estimates["known_download_bytes"] + plan.estimates["output_upper_bound_bytes"]*2 + plan.estimates.get('intermediate_upper_bound_bytes',0)
    if shutil.disk_usage(ctx.project_path).free < needed:
        raise ValueError("Insufficient space for retained inputs, staging and output")
    store.update(job_id, {"status": "running", "started_at": job["started_at"] or now()}, event="started", expected={"pending", "running"})
    cancelled = lambda: store.job(job_id)["status"] == "cancelling"
    staging = ctx.project_path / "data" / ".acquisition" / job_id
    checkpoint = job.get("checkpoints", {})
    retained = [AcquisitionReceipt.model_validate(row) for value in checkpoint.values() for row in (value if isinstance(value,list) else [value])]
    retained.extend(AcquisitionReceipt.model_validate(d['receipt']) for s in plan.selections for d in s.discovery if 'receipt' in d)
    for receipt in retained:
        store.verify_blob(receipt.sha256)
    transport = Transport(store, cancelled=cancelled, max_bytes=plan.estimates["maximum_asset_bytes"], max_total_bytes=plan.estimates['maximum_job_bytes'], retained=retained)
    outputs, results, findings = [], [], []
    for selection in plan.selections:
        sid = selection.id
        stage(store, job_id, sid, "prefetch_scan", "succeeded", "Frozen source inventory and runtime verified")
        stage(store, job_id, sid, "fetch", "running", f"Acquiring {selection.product.name}")
        receipts = acquire(selection, transport, checkpoint, lambda c: store.update(job_id, {"checkpoints": c}, event="acquisition_checkpoint"))
        # Multiple selections and documentary references can retain the same
        # object. Budget unique preserved bytes, consistently with the CAS.
        for receipt in receipts:
            transport.account(receipt)
        stored_receipts = store.job(job_id).get("receipts", {})
        stored_receipts[sid] = [r.model_dump(mode="json") for r in receipts]
        store.update(job_id, {"receipts": stored_receipts}, event="receipts")
        stage(store, job_id, sid, "fetch", "succeeded")
        stage(store, job_id, sid, "raw_metadata", "succeeded", "Exact provider responses retained with SHA-256 receipts")
        stage(store, job_id, sid, "process", "running")
        workdir = staging / sid
        items, checks, issues = run_selection(selection, receipts, plan, store, workdir, cancelled)
        for item in items:
            item["selection_id"] = sid
            item["file"] = sid+"/"+item["file"]
            if item.get("gap_mask"):
                item["gap_mask"] = sid+"/"+item["gap_mask"]
            if item.get("extent_gap"):
                item["extent_gap"] = sid+"/"+item["extent_gap"]
        outputs.extend(items)
        results.extend(checks)
        findings.extend(issues)
        partial = {"schema_version": "zeus.acquisition/2.0", "policy": plan.policy, "results": results, "findings": [f.model_dump(mode="json") for f in findings]}
        store.update(job_id, {"partial_report": partial, "outputs": outputs, "staging": str(staging)}, event="selection_validation_checkpoint")
        stage(store, job_id, sid, "process", "succeeded")
        stage(store, job_id, sid, "validation", "succeeded" if not any(f.severity == "block" for f in issues) else "failed", "Complete scientific-content scan finished")
        stage(store, job_id, sid, "processed_metadata", "succeeded")
    report_body = {"schema_version": "zeus.acquisition/2.0", "policy": plan.policy, "results": results, "findings": [f.model_dump(mode="json") for f in findings]}
    report = {**report_body, "report_hash": digest(report_body)}
    atomic_json(staging / "report.json", report)
    store.update(job_id, {"report": report, "outputs": outputs, "staging": str(staging)}, event="validated")
    if cancelled():
        raise Cancelled("Cancelled before publication")
    if any(f.severity == "block" for f in findings):
        raise ValueError("Mandatory validation failed; inspect the report. Outputs were not published.")
    if any(f.severity == "acknowledgement" for f in findings):
        store.update(job_id, {"status": "awaiting_approval"}, event="awaiting_gap_approval", expected={"running"})
        return
    publish(store, job_id)


def publish(store, job_id):
    job = store.job(job_id)
    plan = FetchPlan.model_validate(store.plan(job["plan_id"]))
    ctx = validate_frozen_context(plan)
    report = job["report"]
    body = {k: v for k, v in report.items() if k != "report_hash"}
    if digest(body) != report["report_hash"]:
        raise ValueError("Validation report integrity failure")
    required = [Finding.model_validate(f) for f in report["findings"]]
    for selection in plan.selections:
        states = job['categories'][selection.id]['stages']
        if any(states[name]['status'] != 'succeeded' for name in STAGES if name != 'layer_publish'):
            raise ValueError('Publication requires every mandatory stage for every selection')
    acknowledged = [a for a in job["approvals"] if a["kind"] == "report" and a["hash"] == report["report_hash"]]
    validate_acknowledgements(required, acknowledged[-1]["finding_ids"] if acknowledged else [])
    generation_dir = ctx.project_path / "data" / "generations" / job_id
    staging = Path(job["staging"])
    base = generation_dir if generation_dir.exists() else staging
    for item in job["outputs"]:
        if file_hash(base / item["file"]) != item["sha256"]:
            raise ValueError("Validated output changed before publication")
        if item.get("gap_mask") and file_hash(base / item["gap_mask"]) != item["gap_sha256"]:
            raise ValueError("Validated gap mask changed before publication")
        if item.get("extent_gap") and file_hash(base / item["extent_gap"]) != item["extent_gap_sha256"]:
            raise ValueError("Validated extent gap changed before publication")
        for key in ('file', 'gap_mask', 'extent_gap'):
            if item.get(key):
                with (base / item[key]).open('r+b') as artifact:
                    os.fsync(artifact.fileno())
    manifest_path = ctx.project_path / "data" / "active-generation.json"
    from .readers import active_datasets,companion_path
    old_datasets = active_datasets(ctx.project_path)
    selection_ids = {s.id for s in plan.selections}
    previous = [d for d in old_datasets if d.get("selection_id") not in selection_ids]
    # A new selection must not carry a damaged older dataset into a newly
    # trusted generation. Validate the current immutable pointer and every
    # artifact retained by the new generation before any commit.
    for item in previous:
        retained=[(item['path'],item['sha256'])]
        if item.get('gap_mask'):
            retained.append((companion_path(item,'gap_mask'),item['gap_sha256']))
        if item.get('extent_gap'):
            retained.append((companion_path(item,'extent_gap'),item['extent_gap_sha256']))
        for name,sha in retained:
            path=(ctx.project_path/name).resolve()
            if not path.is_relative_to(ctx.project_path.resolve()) or not path.is_file() or file_hash(path)!=sha:
                raise ValueError('Previously published artifact failed integrity verification; publication stopped')
    outputs = []
    for item in job["outputs"]:
        item = dict(item)
        item['aoi']=plan.aoi
        item['aoi_hash']=plan.aoi_hash
        item["path"] = f"data/generations/{job_id}/{item['file']}"
        if item.get("gap_mask"):
            item["gap_path"] = f"data/generations/{job_id}/{item['gap_mask']}"
        if item.get('extent_gap'):
            item['extent_gap_path']=f"data/generations/{job_id}/{item['extent_gap']}"
        item["validation_status"] = "passed_with_acknowledged_limitations" if job["approvals"][0]["finding_ids"] or acknowledged else "passed"
        outputs.append(item)
    manifest = {"schema_version": "zeus.acquisition/2.0", "generation": job_id, "project": job["project"], "plan_hash": plan.plan_hash,
                "report_hash": report["report_hash"], "datasets": previous+outputs, "approvals": job["approvals"], "receipts": job["receipts"]}
    # One SQLite write transaction serializes cancellation against the file-system commit.
    # Recovery checks the manifest if the process dies after rename but before DB commit.
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        current = json.loads(row[0])
        if current["status"] not in ("running", "publishing"):
            raise Cancelled("Publication prevented by job state")
        generation_dir.parent.mkdir(parents=True, exist_ok=True)
        if not generation_dir.exists():
            atomic_json(staging / "plan.json", plan.model_dump(mode="json"))
            atomic_json(staging / "manifest.json", manifest)
            if not staging.resolve().is_relative_to(ctx.project_path.resolve()) or not generation_dir.resolve().is_relative_to(ctx.project_path.resolve()):
                raise ValueError('Generation move resolves outside its project')
            durable_replace(staging, generation_dir)
        atomic_json(manifest_path, manifest)
        for category in current["categories"].values():
            category["status"] = "succeeded"
            category["stages"]["layer_publish"] = {"status": "succeeded", "completed_at": now()}
        current.update({"status": "succeeded", "progress": 1, "completed_at": now(), "updated_at": now(), "manifest": str(generation_dir / "manifest.json"), "categories": current["categories"]})
        c.execute("UPDATE jobs SET state=?,payload=? WHERE id=?", ("succeeded", canonical(current).decode(), job_id))
        store._event(c, job_id, "published", {"generation": job_id, "report_hash": report["report_hash"]})


def reconcile_committed(store, job):
    from ..utils import resolve_project_path
    root = resolve_project_path(job['project'])
    pointer = root / 'data/active-generation.json' if root else None
    committed = json.loads(pointer.read_text(encoding='utf-8')) if pointer and pointer.exists() else None
    if not committed or committed.get('generation') != job['id']:
        return False
    generation = root / 'data/generations' / job['id']
    immutable = json.loads((generation / 'manifest.json').read_text(encoding='utf-8'))
    report = json.loads((generation / 'report.json').read_text(encoding='utf-8'))
    if immutable != committed or committed.get('plan_hash') != job['plan_hash'] or committed.get('report_hash') != report['report_hash'] or digest({k:v for k,v in report.items() if k!='report_hash'}) != report['report_hash']:
        raise ValueError('Committed manifest/report integrity failure during recovery')
    for item in committed['datasets']:
        pairs=[(item['path'],item['sha256'])]
        if item.get('gap_mask'):
            pairs.append((str(Path(item['path']).parent / Path(item['gap_mask']).name),item['gap_sha256']))
        if item.get('extent_gap'):
            pairs.append((str(Path(item['path']).parent / Path(item['extent_gap']).name),item['extent_gap_sha256']))
        for name, sha in pairs:
            artifact=(root/name).resolve()
            if not artifact.is_relative_to(root.resolve()) or file_hash(artifact)!=sha:
                raise ValueError('Committed generation integrity failure during recovery')
    categories=job['categories']
    for category in categories.values():
        category['status']='succeeded'
        category['stages']['layer_publish']={'status':'succeeded','message':'Verified atomic manifest commit recovered','completed_at':now()}
    store.update(job['id'], {'status':'succeeded','progress':1,'completed_at':now(),'manifest':str(generation/'manifest.json'),'categories':categories}, event='recovered_commit')
    return True


def recover(store):
    for job in store.jobs(active=True):
        if reconcile_committed(store, job):
            continue
        if job['status']=='running':
            report=job.get('report')
            validated=report and all(stage['status']=='succeeded' for category in job['categories'].values() for name,stage in category['stages'].items() if name!='layer_publish')
            if validated:
                if any(f['severity']=='block' for f in report['findings']):
                    fail_job(store,job['id'],ValueError('Recovered mandatory validation failure'))
                    continue
                requires_review=any(f['severity']=='acknowledgement' for f in report['findings'])
                approved=any(a['kind']=='report' and a['hash']==report['report_hash'] for a in job['approvals'])
                state='awaiting_approval' if requires_review and not approved else 'publishing'
                store.update(job['id'],{'status':state},event='resume_validated_generation')
            else:
                store.update(job['id'], {'status':'pending'}, event='resume_from_verified_receipts')


def fail_job(store, job_id, exc):
    job = store.job(job_id)
    if reconcile_committed(store, job):
        return
    categories = job["categories"]
    for category in categories.values():
        if category["status"] == "running":
            category["status"] = "failed"
        for item in category["stages"].values():
            if item["status"] == "running":
                item.update({"status":"failed","message":str(exc),"completed_at":now()})
    changes = {"status":"failed","error":str(exc),"completed_at":now(),"categories":categories}
    if not job.get("report"):
        finding = Finding(id="job:mandatory-stage-incomplete",rule="complete-mandatory-execution",severity="block",message=str(exc),evidence={"exception_type":type(exc).__name__,"completed_stages":categories})
        body=job.get('partial_report', {"schema_version":"zeus.acquisition/2.0","policy":"complete-extract/1.0","results":[],"findings":[]})
        body['findings'] = body['findings'] + [finding.model_dump(mode="json")]
        changes["report"]={**body,"report_hash":digest(body)}
    store.update(job_id,changes,event="failed")


def worker_main():
    store = Store()
    lock = (store.root / "worker.lock").open("a+b")
    lock.seek(0)
    if os.fstat(lock.fileno()).st_size == 0:
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return
    recover(store)
    from . import discovery_jobs
    discovery_jobs.recover(store)
    while True:
        from .planning import implementation_files
        if {p.name:file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))} != implementation_files():
            return  # The API supervisor starts a worker with the current implementation.
        for job in store.jobs(active=True):
            if job["status"] == "awaiting_approval":
                continue
            try:
                execute(store, job["id"])
            except Cancelled as exc:
                store.update(job["id"], {"status": "cancelled", "error": str(exc), "completed_at": now()}, event="cancelled")
            except Exception as exc:
                # Persisting the failure is mandatory. A ledger error stops this worker.
                fail_job(store,job["id"],exc)
        for discovery in discovery_jobs.active(store):
            discovery_jobs.execute(store,discovery['id'])
        time.sleep(0.5)


def start_worker():
    store = Store()
    from ...cloud_workspace import workspace_path
    workspace = workspace_path()
    environment = os.environ.copy()
    if workspace:
        environment.update({'VULCAN_WORKER_WORKSPACE': str(workspace), 'AGRS_PROJECTS_ROOT': str(workspace/'Projects'),
                            'ZEUS_RESEARCH_STORE': str(workspace/'acquisition'), 'AGRS_DBS_ROOT': str(workspace/'datasets'),
                            'ZEUS_WORKSPACE_SETTINGS': str(workspace/'workspace.json'), 'AGRS_TILE_CACHE_DIR': str(workspace/'tiles')})
    log = (store.root / "worker.log").open("ab")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        return subprocess.Popen([sys.executable, "-c", "from api.dataset_fetch.research.worker import worker_main; worker_main()"], cwd=Path(__file__).resolve().parents[3],
                                stdout=log, stderr=log, creationflags=flags, env=environment,
                                start_new_session=bool(workspace) and os.name != 'nt')
    finally:
        log.close()


if __name__ == "__main__":
    worker_main()
