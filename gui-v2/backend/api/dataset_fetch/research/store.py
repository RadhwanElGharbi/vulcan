from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .contracts import canonical, digest, file_hash, now

ROOT = next((p for p in Path(__file__).resolve().parents if (p / "gui-v2").is_dir()), Path(__file__).resolve().parent.parent)


def durable_replace(source: Path, destination: Path):
    """Same-filesystem atomic rename with the platform's durability request."""
    source, destination = Path(source), Path(destination)
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError('Atomic publication requires the same filesystem')
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
        move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move.restype = wintypes.BOOL
        if not move(str(source.absolute()), str(destination.absolute()), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, destination)
        for directory in {source.parent, destination.parent}:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".commit-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(canonical(value))
            f.flush()
            os.fsync(f.fileno())
        durable_replace(Path(name), path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Store:
    def __init__(self, root: Path | None = None):
        workspace = None
        if root is None:
            from ...cloud_workspace import workspace_path
            workspace = workspace_path()
        self.root = root or (workspace / 'acquisition' if workspace else Path(os.environ.get("ZEUS_RESEARCH_STORE", ROOT / ".runtime" / "acquisition")))
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "ledger.sqlite"
        with self.connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, hash TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project TEXT NOT NULL, plan_id TEXT NOT NULL,
                    state TEXT NOT NULL, payload TEXT NOT NULL, idempotency TEXT NOT NULL, request_hash TEXT NOT NULL,
                    UNIQUE(project,idempotency));
                CREATE UNIQUE INDEX IF NOT EXISTS active_project ON jobs(project)
                    WHERE state IN ('pending','running','awaiting_approval','cancelling','publishing');
                CREATE TABLE IF NOT EXISTS events(job_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
                    hash TEXT NOT NULL, PRIMARY KEY(job_id,seq));
                CREATE TABLE IF NOT EXISTS object_cache(identity TEXT PRIMARY KEY, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS discoveries(id TEXT PRIMARY KEY, project TEXT NOT NULL, state TEXT NOT NULL,
                    payload TEXT NOT NULL, idempotency TEXT NOT NULL, request_hash TEXT NOT NULL, UNIQUE(project,idempotency));
                CREATE UNIQUE INDEX IF NOT EXISTS active_discovery_project ON discoveries(project)
                    WHERE state IN ('pending','running','cancelling');
            """)

    @staticmethod
    def object_identity(url,etag,size,last_modified):
        if not etag or etag.startswith('W/') or size is None: return None
        return digest({'url':url,'etag':etag,'size':size,'last_modified':last_modified})

    def cache_receipt(self,receipt):
        identity=self.object_identity(receipt.source_url,receipt.headers.get('etag'),receipt.size,receipt.headers.get('last-modified'))
        if identity is None: return
        self.verify_blob(receipt.sha256)
        with self.connect() as c:
            previous=c.execute('SELECT receipt FROM object_cache WHERE identity=?',(identity,)).fetchone()
            if previous and json.loads(previous[0])['sha256']!=receipt.sha256:
                raise ValueError('Provider reused the same object identity for different bytes')
            c.execute('INSERT OR REPLACE INTO object_cache VALUES(?,?)',(identity,canonical(receipt).decode()))

    def cached_receipt(self,asset):
        from .contracts import AcquisitionReceipt
        identity=self.object_identity(asset.url,asset.etag,asset.size,asset.last_modified)
        if identity is None: return None
        with self.connect() as c: row=c.execute('SELECT receipt FROM object_cache WHERE identity=?',(identity,)).fetchone()
        if not row: return None
        receipt=AcquisitionReceipt.model_validate(json.loads(row[0]))
        path=self.verify_blob(receipt.sha256)
        if path.stat().st_size!=asset.size: raise ValueError('Cached input size contradicts the frozen asset')
        return receipt.model_copy(update={'asset_id':asset.id})

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        if c.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def plan(self, plan_id):
        with self.connect() as c:
            row = c.execute("SELECT payload FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown plan")
        value = json.loads(row[0])
        body = {k: v for k, v in value.items() if k not in ("plan_id", "plan_hash")}
        if digest(body) != value["plan_hash"]:
            raise ValueError("Stored plan integrity failure")
        return value

    def save_plan(self, plan):
        value = plan.model_dump(mode="json")
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO plans VALUES(?,?,?)", (plan.plan_id, plan.plan_hash, canonical(value).decode()))

    def job(self, job_id):
        with self.connect() as c:
            row = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown job")
        return json.loads(row[0])

    def jobs(self, active=False):
        with self.connect() as c:
            rows = c.execute("SELECT payload FROM jobs" + (" WHERE state IN ('pending','running','awaiting_approval','cancelling','publishing')" if active else "")).fetchall()
        return [json.loads(r[0]) for r in rows]

    @staticmethod
    def _event(c, job_id, kind, detail):
        previous = c.execute("SELECT seq,hash FROM events WHERE job_id=? ORDER BY seq DESC LIMIT 1", (job_id,)).fetchone()
        seq = previous[0]+1 if previous else 1
        entry = {"sequence": seq, "at": now(), "kind": kind, "detail": detail, "previous": previous[1] if previous else None}
        c.execute("INSERT INTO events VALUES(?,?,?,?)", (job_id, seq, canonical(entry).decode(), digest(entry)))

    def create_job(self, job, request):
        request_hash = digest(request)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT payload,request_hash FROM jobs WHERE project=? AND idempotency=?", (job["project"], request["idempotency_key"])).fetchone()
            if old:
                if old[1] != request_hash:
                    raise ValueError("Idempotency key was already used for different inputs")
                return json.loads(old[0])
            c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (job["id"], job["project"], job["plan_id"], job["status"], canonical(job).decode(), request["idempotency_key"], request_hash))
            self._event(c, job["id"], "confirmed", request)
        return job

    def update(self, job_id, changes, event="state", expected=None):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown job")
            job = json.loads(row[0])
            if expected is not None and job["status"] not in expected:
                raise ValueError(f"Job is {job['status']}, cannot apply {event}")
            job.update(changes)
            job["updated_at"] = now()
            c.execute("UPDATE jobs SET state=?,payload=? WHERE id=?", (job["status"], canonical(job).decode(), job_id))
            self._event(c, job_id, event, changes)
        return job

    def events(self, job_id):
        with self.connect() as c:
            rows = c.execute("SELECT payload,hash FROM events WHERE job_id=? ORDER BY seq", (job_id,)).fetchall()
        previous = None
        result = []
        for row in rows:
            value = json.loads(row[0])
            if value["previous"] != previous or digest(value) != row[1]:
                raise ValueError("Audit event chain integrity failure")
            result.append({**value, "hash": row[1]})
            previous = row[1]
        return result

    def blob_path(self, sha256):
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("Invalid blob digest")
        return self.root / "blobs" / sha256[:2] / sha256

    def retain(self, path: Path):
        # Every caller, including SDK downloads and legacy snapshots, must make
        # bytes durable before the ledger can reference the content object.
        with path.open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        sha = file_hash(path)
        target = self.blob_path(sha)
        if path.resolve()==target.resolve():
            return sha,target
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if file_hash(target) != sha:
                raise ValueError("Corrupt content store object")
            path.unlink()
        else:
            durable_replace(path, target)
        return sha, target

    def verify_blob(self, sha):
        path = self.blob_path(sha)
        if not path.is_file() or file_hash(path) != sha:
            raise ValueError(f"Missing or corrupt replay input {sha}")
        return path
