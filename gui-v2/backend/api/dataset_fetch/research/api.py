from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Header
from fastapi.responses import FileResponse, Response, StreamingResponse, JSONResponse
from starlette.background import BackgroundTask

from .contracts import AcceptRequest, ExecuteRequest, FetchPlan, Finding, PlanRequest, digest, now, validate_acknowledgements
from .planning import build_plan
from .registry import public_registry, registry
from .store import ROOT, Store, atomic_json
from .worker import TERMINAL, new_job, validate_frozen_context

router = APIRouter(tags=["scientific acquisition"])


def error(exc):
    if isinstance(exc, KeyError):
        return HTTPException(404, str(exc))
    if isinstance(exc, sqlite3.IntegrityError):
        if 'discoveries' in str(exc):
            return HTTPException(409, 'This project already has an active discovery. Reopen source review to reconnect, or cancel it before starting another.')
        return HTTPException(409, "This project already has an active job. Finish or cancel it before starting another.")
    return HTTPException(409 if "changed" in str(exc).lower() else 422, str(exc))


@router.get("/dataset-sources")
def sources(project: str | None = None):
    if project is None:
        return public_registry()
    from ..utils import _load_project_context
    return public_registry(_load_project_context(project).iso3_list)


@router.get("/dataset-sources/{product_id}/availability")
def source_availability(product_id: str, project: str | None = None, year: int | None = Query(None, ge=1900, le=2200), month: int | None = Query(None, ge=1, le=12), country: str | None = None, variables: str | None = None):
    from .availability import availability
    try:
        return availability(product_id, project, year, month, country, variables.split(',') if variables else None)
    except KeyError as exc:
        raise HTTPException(404, 'Product or provider availability metadata not found') from exc
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/dataset-sources/audit")
def source_audit():
    path = ROOT / "docs" / "datasets" / "catalogue-assessment.json"
    if not path.exists():
        return {"status": "pending", "entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/projects/{project}/dataset-fetch/plan", response_model=FetchPlan)
def create_plan(project: str, request: PlanRequest, background: bool = False, idempotency_key: str | None = Header(default=None)):
    try:
        if background:
            from .discovery_jobs import submit
            return JSONResponse(submit(Store(),project,request,idempotency_key),status_code=202)
        return build_plan(project, request, Store())
    except (ValueError, KeyError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise error(exc) from exc


@router.get('/dataset-discovery-jobs/{identity}')
def discovery_status(identity: str):
    from .discovery_jobs import get
    store=Store()
    try:return {**get(store,identity),'events':store.events(identity)}
    except (ValueError,KeyError) as exc:raise error(exc) from exc


@router.get('/projects/{project}/dataset-discovery-jobs')
def project_discoveries(project: str):
    with Store().connect() as c:
        rows=c.execute('SELECT payload FROM discoveries WHERE project=? ORDER BY rowid DESC LIMIT 20',(project,)).fetchall()
    return {'jobs':[json.loads(row[0]) for row in rows]}


@router.delete('/dataset-discovery-jobs/{identity}',status_code=202)
def cancel_discovery(identity: str):
    from .discovery_jobs import get,update,ACTIVE
    store=Store()
    try:
        value=get(store,identity)
        if value['status'] not in ACTIVE:return Response(status_code=204)
        return update(store,identity,{'status':'cancelling','stage':'stopping'},'discovery_cancel_requested',ACTIVE)
    except (ValueError,KeyError) as exc:raise error(exc) from exc


@router.get("/dataset-plans/{plan_id}")
def get_plan(plan_id: str):
    try:
        return Store().plan(plan_id)
    except (ValueError, KeyError) as exc:
        raise error(exc) from exc


@router.post("/projects/{project}/dataset-fetch")
def execute_plan(project: str, request: ExecuteRequest):
    store = Store()
    try:
        plan = FetchPlan.model_validate(store.plan(request.plan_id))
        if plan.project != project or plan.plan_hash != request.plan_hash:
            raise ValueError("Plan does not match project or confirmed hash")
        validate_frozen_context(plan)
        findings = plan.findings + [f for s in plan.selections for f in s.findings]
        validate_acknowledgements(findings, request.acknowledged_findings)
        job = store.create_job(new_job(plan, request), request.model_dump(mode="json"))
        return {"job_id": job["id"]}
    except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
        raise error(exc) from exc


@router.get("/dataset-jobs/active")
def active_jobs():
    jobs = Store().jobs(active=True)
    return {"active_jobs": {j["project"]: {"job_id": j["id"], **{k: j.get(k) for k in ("status", "progress", "current_category", "started_at", "updated_at")}} for j in jobs}, "count": len(jobs)}


@router.get('/projects/{project}/dataset-jobs')
def project_jobs(project: str, offset: int = Query(0, ge=0)):
    from .readers import selection_label
    store = Store()
    with store.connect() as connection:
        rows = connection.execute('SELECT payload FROM jobs WHERE project=? ORDER BY rowid DESC LIMIT 21 OFFSET ?', (project, offset)).fetchall()
    jobs = []
    for row in rows[:20]:
        job = json.loads(row[0])
        plan = store.plan(job['plan_id'])
        jobs.append({k: job[k] for k in ('id', 'status', 'updated_at')})
        jobs[-1]['products'] = [selection_label(s['product']['name'],s['parameters']) for s in plan['selections']]
    return {'jobs': jobs, 'has_more': len(rows) > 20}


@router.get("/dataset-jobs/{job_id}")
def get_job(job_id: str):
    try:
        job = Store().job(job_id)
        return {k: v for k, v in job.items() if k not in ("checkpoints", "receipts", "staging")}
    except KeyError as exc:
        raise error(exc) from exc


@router.get("/dataset-jobs/{job_id}/stream")
async def stream_job(job_id: str):
    get_job(job_id)
    async def generate():
        previous = None
        while True:
            job = await asyncio.to_thread(get_job, job_id)
            serialized = json.dumps(job)
            if serialized != previous:
                yield "data: " + serialized + "\n\n"
                previous = serialized
            else:
                yield ": keepalive\n\n"
            if job["status"] in TERMINAL:
                break
            await asyncio.sleep(1)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/projects/{project}/events/stream")
async def stream_project(project: str):
    async def generate():
        previous = None
        while True:
            jobs = await asyncio.to_thread(Store().jobs)
            state = [{k: j[k] for k in ("id", "status", "updated_at")} for j in jobs if j["project"] == project]
            current = digest(state)
            if current != previous:
                yield "data: " + json.dumps({"type": "dataset_jobs_changed", "jobs": state}) + "\n\n"
                previous = current
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.delete("/dataset-jobs/{job_id}", status_code=202)
def cancel_job(job_id: str):
    store = Store()
    try:
        job = store.job(job_id)
        if job["status"] in TERMINAL:
            return Response(status_code=204)
        store.update(job_id, {"status": "cancelling"}, event="cancel_requested", expected={"pending", "running", "awaiting_approval", "publishing", "cancelling"})
        return {"status": "cancelling"}
    except (ValueError, KeyError) as exc:
        raise error(exc) from exc


@router.post("/dataset-jobs/{job_id}/accept")
def accept_result(job_id: str, request: AcceptRequest):
    store = Store()
    try:
        job = store.job(job_id)
        report = job.get("report")
        if not report or report["report_hash"] != request.report_hash:
            raise ValueError("Approval does not match the current validation report")
        validate_acknowledgements([Finding.model_validate(f) for f in report["findings"]], request.acknowledged_findings)
        approvals = job["approvals"] + [{"kind": "report", "hash": request.report_hash, "finding_ids": request.acknowledged_findings, "at": now()}]
        store.update(job_id, {"status": "publishing", "approvals": approvals}, event="accepted_report", expected={"awaiting_approval"})
        return {"status": "publishing"}
    except (ValueError, KeyError) as exc:
        raise error(exc) from exc


@router.get("/dataset-jobs/{job_id}/report")
def report(job_id: str):
    store = Store()
    try:
        job = store.job(job_id)
        return {"plan": store.plan(job["plan_id"]), "validation": job.get("report"), "approvals": job["approvals"], "events": store.events(job_id), "status": job["status"], "error": job.get("error"), "outputs": job.get("outputs", [])}
    except (ValueError, KeyError) as exc:
        raise error(exc) from exc


def job_base(job):
    from ..utils import resolve_project_path
    project = resolve_project_path(job["project"])
    if not project:
        raise HTTPException(404, "Project unavailable")
    generation = project / "data" / "generations" / job["id"]
    return generation if generation.exists() else project / "data" / ".acquisition" / job["id"]


@router.get("/dataset-jobs/{job_id}/artifacts/{artifact:path}")
def artifact(job_id: str, artifact: str):
    store = Store()
    try:
        job = store.job(job_id)
    except KeyError as exc:
        raise error(exc) from exc
    allowed = {item[key]:item[sha] for item in job.get('outputs',[]) for key,sha in (('file','sha256'),('gap_mask','gap_sha256'),('extent_gap','extent_gap_sha256')) if item.get(key)}
    if artifact not in allowed:
        raise HTTPException(404, "Unknown job artifact")
    base = job_base(job).resolve()
    path = (base / artifact).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "Artifact unavailable")
    from .contracts import file_hash
    if file_hash(path)!=allowed[artifact]:
        raise HTTPException(409,'Artifact integrity check failed; the validated bytes are unavailable')
    return FileResponse(path, filename=path.name)


@router.get("/dataset-jobs/{job_id}/bundle")
def bundle(job_id: str):
    store = Store()
    try:
        job = store.job(job_id)
        plan = store.plan(job["plan_id"])
        payload = report(job_id)
    except (ValueError, KeyError) as exc:
        raise error(exc) from exc
    fd, name = tempfile.mkstemp(suffix=".zip", dir=store.root)
    import os
    os.close(fd)
    base = job_base(job)
    try:
        from .bundle import write_bundle
        write_bundle(Path(name), plan, job, payload, store, base)
        return FileResponse(name, filename=f"zeus-{job_id}-provenance.zip", background=BackgroundTask(Path(name).unlink, missing_ok=True))
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


@router.get("/projects/{project}/dataset-status")
def dataset_status(project: str):
    from ..utils import _load_project_context
    from ..models import DATASET_DEFINITIONS
    from .readers import active_datasets
    ctx = _load_project_context(project)
    active = active_datasets(ctx.project_path)
    categories = []
    for key in sorted({p.category for p in registry().values()}):
        matches = [d for d in active if d["category"] == key]
        definition = DATASET_DEFINITIONS.get(key)
        legacy = definition.processed_path(ctx) if definition else None
        present = bool(matches) or bool(legacy and legacy.exists())
        categories.append({"category": key, "label": key.replace("_", " ").title(), "dataset_type": matches[0]["kind"] if matches and matches[0]["kind"] in ("raster", "vector") else "vector" if key in ("roads", "railways", "powerlines", "waterways", "pipelines", "protected_areas", "indigenous_lands") else "raster",
                           "required": False, "present": present, "raw_path": None, "processed_path": matches[0]["path"] if matches else str(legacy) if present else None,
                           "metadata_path": None, "last_modified": None, "description": "Validated acquisition" if matches else "Legacy / unverified" if present else "No acquisition",
                           "provenance_status": "validated" if matches else "legacy_unverified" if present else "absent"})
    return {"project": project, "target_epsg": ctx.target_epsg, "minimum_requirements_met": any(d["category"] == "dem" for d in active), "categories": categories, "protocol_reference": "zeus.acquisition/2.0"}


@router.get("/projects/{project}/datasets/{dataset_id}/download")
def download_dataset(project: str, dataset_id: str):
    from ..utils import resolve_project_path
    from .readers import active_datasets
    from .contracts import file_hash
    root = resolve_project_path(project)
    if root is None:
        raise HTTPException(404, "Unknown project")
    item = next((d for d in active_datasets(root) if d["id"] == dataset_id), None)
    if item is None:
        raise HTTPException(404, "Dataset is not in the committed generation")
    path = root / item["path"]
    if file_hash(path) != item["sha256"]:
        raise HTTPException(409, "Published artifact integrity failure")
    return FileResponse(path, filename=path.name, headers={"ETag": '"'+item["sha256"]+'"'})
