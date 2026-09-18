from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from .models import DatasetDefinition, FetchContext
from .utils import _utc_iso, _utc_now


# ---------------------------------------------------------------------------
# Job state + registry
# ---------------------------------------------------------------------------

_PERSIST_DB_PATH = Path(os.getenv(
    "DATASET_JOBS_DB",
    str(Path(__file__).resolve().parents[2] / ".dataset_jobs.sqlite"),
))
_PERSIST_DEBOUNCE_S = 1.0
_persist_timer: Optional[threading.Timer] = None
_persist_lock = threading.Lock()


@dataclass
class DatasetJobState:
    id: str
    project: str
    categories: List[str]
    force: bool
    overrides: Dict[str, str] = field(default_factory=dict)
    cancel_requested: bool = False
    status: Literal["pending", "running", "succeeded", "failed", "partial"] = "pending"
    logs: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utc_now)
    started_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=_utc_now)
    completed_at: Optional[datetime] = None
    current_category: Optional[str] = None
    progress: float = 0.0
    error: Optional[str] = None
    category_states: Dict[str, Dict[str, Optional[str]]] = field(default_factory=dict)
    total_log_count: int = 0


JOB_REGISTRY: Dict[str, DatasetJobState] = {}
PROJECT_ACTIVE_JOBS: Dict[str, str] = {}
JOB_LOCK = threading.RLock()
JOB_LOCK_TIMEOUT_S = float(os.getenv("DATASET_JOB_LOCK_TIMEOUT_S", "5"))


# ---------------------------------------------------------------------------
# SQLite persistence layer
# ---------------------------------------------------------------------------

def _ensure_persist_db() -> None:
    _PERSIST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_PERSIST_DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dataset_jobs (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress REAL DEFAULT 0.0,
                current_category TEXT,
                categories_json TEXT,
                logs_json TEXT,
                error TEXT,
                force INTEGER DEFAULT 0,
                overrides_json TEXT,
                category_states_json TEXT,
                total_log_count INTEGER DEFAULT 0,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT,
                completed_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_project ON dataset_jobs(project)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON dataset_jobs(status)")
        conn.commit()
    finally:
        conn.close()


def _persist_job(job: DatasetJobState) -> None:
    """Write current job state to SQLite."""
    try:
        _ensure_persist_db()
        conn = sqlite3.connect(str(_PERSIST_DB_PATH), timeout=5)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO dataset_jobs
                (id, project, status, progress, current_category, categories_json,
                 logs_json, error, force, overrides_json, category_states_json,
                 total_log_count, created_at, started_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.id,
                job.project,
                job.status,
                job.progress,
                job.current_category,
                json.dumps(job.categories),
                json.dumps(job.logs[-200:]),
                job.error,
                1 if job.force else 0,
                json.dumps(job.overrides),
                json.dumps(job.category_states, default=str),
                job.total_log_count,
                job.created_at.isoformat() if job.created_at else None,
                job.started_at.isoformat() if job.started_at else None,
                job.updated_at.isoformat() if job.updated_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
            ))
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def _schedule_persist(job: DatasetJobState) -> None:
    """Debounced persistence — writes at most once per _PERSIST_DEBOUNCE_S."""
    global _persist_timer
    with _persist_lock:
        if _persist_timer is not None:
            _persist_timer.cancel()
        _persist_timer = threading.Timer(_PERSIST_DEBOUNCE_S, _persist_job, args=(job,))
        _persist_timer.daemon = True
        _persist_timer.start()


def recover_orphaned_jobs() -> List[str]:
    """On startup, mark any 'running'/'pending' jobs as failed (interrupted by restart).

    Returns list of recovered job IDs.
    """
    recovered: List[str] = []
    try:
        _ensure_persist_db()
        conn = sqlite3.connect(str(_PERSIST_DB_PATH), timeout=5)
        try:
            cursor = conn.execute(
                "SELECT id, project FROM dataset_jobs WHERE status IN ('running', 'pending')"
            )
            rows = cursor.fetchall()
            now = _utc_iso()
            for job_id, project in rows:
                conn.execute(
                    "UPDATE dataset_jobs SET status = 'failed', error = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                    ("Interrupted by server restart", now, now, job_id),
                )
                recovered.append(job_id)
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    return recovered


def cleanup_orphaned_staging(projects_root: Path) -> List[str]:
    """Scan all projects for orphaned .staging/ directories and clean them up.

    Returns list of cleaned paths.
    """
    cleaned: List[str] = []
    if not projects_root.exists():
        return cleaned
    try:
        for project_dir in projects_root.iterdir():
            if not project_dir.is_dir():
                continue
            staging = project_dir / "data" / ".staging"
            if not staging.exists():
                continue
            for job_dir in staging.iterdir():
                if not job_dir.is_dir():
                    continue
                job_id = job_dir.name
                if job_id not in JOB_REGISTRY:
                    import shutil
                    shutil.rmtree(job_dir, ignore_errors=True)
                    cleaned.append(str(job_dir))
    except OSError:
        pass
    return cleaned


# Stage names matching frontend expectations
STAGE_NAMES = [
    "prefetch_scan",     # Initial scan/setup
    "fetch",             # Data transfer
    "zeus_ai",           # AI agent operations
    "raw_metadata",      # Metadata extraction
    "validation",        # Integrity check
    "process",           # Geoprocessing
    "processed_metadata", # Indexing
    "layer_publish"      # Map publish
]


def _init_stages() -> Dict[str, Dict[str, Optional[str]]]:
    """Initialize all stages to queued status."""
    return {
        stage: {
            "status": "queued",
            "message": None,
            "started_at": None,
            "completed_at": None,
        }
        for stage in STAGE_NAMES
    }


def _init_category_state() -> Dict[str, Any]:
    """Create initial category state with stages."""
    return {
        "status": "queued",
        "message": None,
        "started_at": None,
        "completed_at": None,
        "stages": _init_stages(),
    }


def _update_stage(job: DatasetJobState, category: str, stage: str, status: str, message: Optional[str] = None) -> None:
    """Update a specific stage's status within a category."""
    with JOB_LOCK:
        cat_state = job.category_states.get(category)
        if not cat_state:
            return
        stages = cat_state.get("stages")
        if not stages or stage not in stages:
            return
        stage_state = stages[stage]
        stage_state["status"] = status
        if message:
            stage_state["message"] = message
        if status == "running" and not stage_state.get("started_at"):
            stage_state["started_at"] = _utc_iso()
        if status in ("succeeded", "failed", "skipped"):
            stage_state["completed_at"] = _utc_iso()
        job.updated_at = _utc_now()
    _schedule_persist(job)


def _log_to_job(
    job: DatasetJobState,
    ctx: FetchContext,
    message: str,
    level: str = "INFO",
    category: Optional[str] = None,
    stage: Optional[str] = None,
) -> None:
    timestamp = _utc_iso()
    cat_tag = f" [{category}]" if category else ""
    stage_tag = f" [{stage}]" if stage else ""
    line = f"[{timestamp}] [{level}] [{job.project}]{cat_tag}{stage_tag} {message}"
    with JOB_LOCK:
        job.logs.append(line)
        job.total_log_count += 1
        if len(job.logs) > 800:
            job.logs = job.logs[-800:]
        job.updated_at = _utc_now()
    try:
        with ctx.log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _cancel_if_requested(job: DatasetJobState, ctx: FetchContext) -> bool:
    if not getattr(job, "cancel_requested", False):
        return False

    message = "Cancelled by user"
    now_iso = _utc_iso()
    with JOB_LOCK:
        for category, state in job.category_states.items():
            status = state.get("status")
            if status in (None, "queued", "running"):
                state["status"] = "cancelled"
                state["message"] = message
                state["completed_at"] = state.get("completed_at") or now_iso
                # Also cancel all stages that haven't completed
                stages = state.get("stages")
                if stages:
                    for stage_name, stage_state in stages.items():
                        s_status = stage_state.get("status")
                        if s_status in (None, "queued", "running"):
                            stage_state["status"] = "cancelled"
                            stage_state["message"] = message
                            stage_state["completed_at"] = stage_state.get("completed_at") or now_iso
        job.status = "failed"
        job.error = job.error or message
        job.current_category = None
        job.completed_at = _utc_now()
        job.updated_at = job.completed_at
    _log_to_job(job, ctx, "Cancellation request acknowledged; terminating job.")
    return True


def _run_command(cmd: List[str], cwd: Path, job: DatasetJobState, ctx: FetchContext, label: str) -> None:
    display = " ".join(cmd)
    _log_to_job(job, ctx, f"{label}: {display}")
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    for line in process.stdout:
        _log_to_job(job, ctx, f"{label}: {line.rstrip()}")
    rc = process.wait()
    if rc != 0:
        raise RuntimeError(f"{label} failed (exit code {rc})")


def _run_command_capture(
    cmd: List[str], cwd: Path, job: DatasetJobState, ctx: FetchContext, label: str, timeout: int = 600,
) -> Tuple[int, str]:
    """Run command and return (exit_code, combined_output). Logs output but doesn't raise on failure.

    Args:
        timeout: Maximum wall-clock seconds before the process is killed (default 600).
    """
    display = " ".join(cmd)
    _log_to_job(job, ctx, f"{label}: {display}")
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def _kill_on_timeout() -> None:
        try:
            process.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, _kill_on_timeout)
    timer.daemon = True
    timer.start()

    assert process.stdout is not None
    output_lines: List[str] = []
    try:
        for line in process.stdout:
            stripped = line.rstrip()
            output_lines.append(stripped)
            _log_to_job(job, ctx, f"{label}: {stripped}")
    finally:
        timer.cancel()

    rc = process.wait()
    if rc == -9:
        _log_to_job(job, ctx, f"{label}: killed after {timeout}s timeout")
    return rc, "\n".join(output_lines)


def _ensure_symlink(link_path: Path, target_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    relative = os.path.relpath(target_path, link_path.parent)
    os.symlink(relative, link_path)


def _dataset_ready(defn: DatasetDefinition, ctx: FetchContext) -> bool:
    # Treat either canonical or legacy processed artifacts as satisfying readiness.
    # This prevents regressions when we change naming conventions (e.g., waterways
    # moving from `osm_waterways_*` to source-agnostic `waterways_*`).
    processed_candidates: List[Path] = [defn.processed_path(ctx)]
    for base in getattr(defn, "legacy_processed_basenames", []) or []:
        processed_name = f"{base}_epsg{ctx.target_epsg}_processed.{defn.processed_extension}"
        processed_candidates.append(defn._base_dir(ctx) / "processed" / processed_name)

    for processed in processed_candidates:
        meta = processed.with_suffix(processed.suffix + ".json")
        if processed.exists() and meta.exists() and processed.stat().st_size > 1024:
            return True
    return False


_GDALINFO_STATS_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB


# ---------------------------------------------------------------------------
# Job response helpers
# ---------------------------------------------------------------------------


def _job_to_response(job: DatasetJobState) -> Dict[str, Any]:
    """Convert job state to a response dict."""
    return {
        "id": job.id,
        "project": job.project,
        "status": job.status,
        "progress": job.progress,
        "current_category": job.current_category,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "categories": copy.deepcopy(job.category_states),
        "logs": list(job.logs),
        "total_log_count": job.total_log_count,
        "force": job.force,
        "error": job.error,
        "overrides": job.overrides,
    }


def _get_job_snapshot_sync(job_id: str) -> Optional[Dict[str, Any]]:
    """Synchronous helper to get job snapshot under lock - runs in thread pool."""
    with JOB_LOCK:
        job = JOB_REGISTRY.get(job_id)
        return _job_to_response(job) if job else None

