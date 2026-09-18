"""Opt-in anonymous, expiring workspaces. Never enabled by the desktop launcher."""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

CURRENT_WORKSPACE: ContextVar[Path | None] = ContextVar('vulcan_workspace', default=None)
COOKIE = 'vulcan_workspace'


def enabled():
    return os.getenv('VULCAN_CLOUD_MODE') == '1'


def workspace_path():
    path = CURRENT_WORKSPACE.get()
    # Worker processes receive their workspace explicitly from the supervisor.
    if path is None and os.getenv('VULCAN_WORKER_WORKSPACE'):
        path = Path(os.environ['VULCAN_WORKER_WORKSPACE'])
    if enabled() and path is None:
        raise RuntimeError('Cloud operation requires an isolated workspace context')
    return path


class Sessions:
    def __init__(self, root: Path | None = None):
        value = root or os.getenv('VULCAN_CLOUD_ROOT')
        if not value:
            raise RuntimeError('VULCAN_CLOUD_ROOT must name a dedicated temporary-storage directory')
        self.root = Path(value).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'sessions.sqlite'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, created REAL, expires REAL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def directory(self, identity):
        if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Invalid workspace identity')
        path = self.root / identity
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError('Invalid workspace directory')
        return path

    def create(self):
        import hashlib
        token = secrets.token_urlsafe(32)
        identity = secrets.token_hex(16)
        created = time.time()
        expires = created + int(os.getenv('VULCAN_WORKSPACE_TTL_SECONDS', '86400'))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Expired workspaces still count until the supervisor has removed their files.
            if db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] >= int(os.getenv('VULCAN_MAX_WORKSPACES', '20')):
                raise HTTPException(503, 'Temporary workspace capacity is full. Try again later.')
            self.directory(identity).mkdir()
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (identity, hashlib.sha256(token.encode()).hexdigest(), created, expires))
        return token, {'id': identity, 'created': created, 'expires': expires}

    def lookup(self, token):
        import hashlib
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute('SELECT id,created,expires FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return dict(row) if row and row['expires'] > time.time() else None

    def all(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,created,expires FROM sessions')]

    @staticmethod
    def public(session):
        from datetime import datetime, timezone
        return {'mode': 'temporary_cloud', 'expires_at': datetime.fromtimestamp(session['expires'], timezone.utc).isoformat(),
                'storage_limit_bytes': int(os.getenv('VULCAN_WORKSPACE_MAX_BYTES', str(5 * 1024**3)))}


class CloudMiddleware:
    """Pure ASGI: keep tenant context and cleanup leases through streaming downloads."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not enabled():
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        request = Request(scope)
        if request.url.path == '/api/health':
            return await self.app(scope, receive, send)
        origin = request.headers.get('origin')
        allowed = os.getenv('VULCAN_PUBLIC_ORIGIN', 'https://vulcan.colony.tech')
        if (origin and origin != allowed) or request.headers.get('sec-fetch-site') == 'cross-site':
            return await JSONResponse({'detail': 'Origin not allowed.'}, 403)(scope, receive, send)
        sessions = scope['app'].state.cloud_sessions
        supervisor = getattr(scope['app'].state, 'cloud_supervisor', None)
        if supervisor is not None and supervisor.done():
            return await JSONResponse({'detail': 'The workspace worker supervisor is unavailable.'}, 503)(scope, receive, send)
        session = sessions.lookup(request.cookies.get(COOKIE))
        if request.url.path == '/api/session' and request.method in {'POST', 'GET'}:
            token = None
            if not session and request.method == 'POST':
                try:
                    token, session = sessions.create()
                except HTTPException as exc:
                    return await JSONResponse({'detail': exc.detail}, exc.status_code)(scope, receive, send)
            if not session:
                return await JSONResponse({'detail': 'Workspace expired.'}, 401)(scope, receive, send)
            response = JSONResponse(sessions.public(session), headers={'Cache-Control': 'private, no-store'})
            if token:
                response.set_cookie(COOKIE, token, max_age=int(session['expires'] - time.time()),
                                    secure=os.getenv('VULCAN_INSECURE_TEST_COOKIE') != '1', httponly=True, samesite='strict', path='/api')
            return await response(scope, receive, send)
        if session is None:
            return await JSONResponse({'detail': 'Temporary workspace expired. Reload to start a new workspace.'}, 401)(scope, receive, send)
        # Never allow native dialogs or server filesystem selection on the website.
        if request.url.path.startswith('/api/workspace/') or (request.url.path == '/api/workspace' and request.method != 'GET'):
            return await JSONResponse({'detail': 'Cloud workspaces use temporary storage.'}, 403)(scope, receive, send)
        identity = session['id']
        leases = scope['app'].state.cloud_leases
        leases[identity] = leases.get(identity, 0) + 1
        marker = CURRENT_WORKSPACE.set(sessions.directory(identity))
        scope['state'] = {**scope.get('state', {}), 'cloud_session': sessions.public(session)}
        try:
            import asyncio
            # Long-lived event streams must not prevent an expired workspace being removed.
            started = False
            async def wrapped_send(message):
                nonlocal started
                if message['type'] == 'http.response.start':
                    started = True
                    message['headers'] = [(k, v) for k, v in message['headers'] if k.lower() != b'cache-control'] + [(b'cache-control', b'private, no-store')]
                await send(message)
            try:
                async with asyncio.timeout(max(0.01, session['expires'] - time.time())):
                    await self.app(scope, receive, wrapped_send)
            except TimeoutError:
                if not started:
                    await JSONResponse({'detail': 'Temporary workspace expired.'}, 410)(scope, receive, send)
                else:
                    await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        finally:
            CURRENT_WORKSPACE.reset(marker)
            leases[identity] -= 1


def stop_worker_tree(process):
    import signal
    import subprocess
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=True, capture_output=True)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=15)


async def supervise_cloud(app):
    import asyncio
    import shutil
    from .dataset_fetch.research.worker import start_worker
    from .dataset_fetch.research.store import Store
    sessions = app.state.cloud_sessions
    processes = {}
    def disk_usage(directory):
        total = 0
        for path in directory.rglob('*'):
            try:
                if path.is_file(): total += path.stat().st_size
            except FileNotFoundError:
                pass  # Atomic tile/download replacements may remove a temporary file while scanning.
        return total
    try:
        while True:
            for session in sessions.all():
                identity = session['id']
                directory = sessions.directory(identity)
                process = processes.get(identity)
                if session['expires'] <= time.time():
                    if process:
                        await asyncio.to_thread(stop_worker_tree, process)
                        processes.pop(identity)
                    if app.state.cloud_leases.get(identity, 0):
                        continue
                    # Validated immediate child of the dedicated cloud root; no local-project paths.
                    if directory.exists():
                        await asyncio.to_thread(shutil.rmtree, directory)
                    with sessions.connect() as db:
                        db.execute('DELETE FROM sessions WHERE id=?', (identity,))
                    continue
                if process and process.poll() is not None:
                    processes.pop(identity); process = None
                marker = CURRENT_WORKSPACE.set(directory)
                try:
                    store = Store()
                    usage = await asyncio.to_thread(disk_usage, directory)
                    over_limit = usage > int(os.getenv('VULCAN_WORKSPACE_MAX_BYTES', str(5 * 1024**3)))
                    if over_limit:
                        if process:
                            await asyncio.to_thread(stop_worker_tree, process)
                            processes.pop(identity)
                        # Preserve reports and files for download; never publish a killed job.
                        for job in store.jobs(active=True):
                            store.update(job['id'], {'status': 'failed', 'error': 'Temporary storage limit reached; download your results.'}, event='cloud_storage_limit')
                        from .dataset_fetch.research import discovery_jobs
                        for discovery in discovery_jobs.active(store):
                            discovery_jobs.update(store, discovery['id'], {'status': 'failed', 'error': 'Temporary storage limit reached.'}, 'cloud_storage_limit')
                        continue
                    with store.connect() as db:
                        work = db.execute("SELECT 1 FROM jobs WHERE state IN ('pending','running','publishing','cancelling') UNION ALL SELECT 1 FROM discoveries WHERE state IN ('pending','running','cancelling') LIMIT 1").fetchone()
                    if not process and work and len(processes) < int(os.getenv('VULCAN_MAX_WORKERS', '2')):
                        processes[identity] = start_worker()
                    elif process and not work:
                        await asyncio.to_thread(stop_worker_tree, process)
                        processes.pop(identity)
                finally:
                    CURRENT_WORKSPACE.reset(marker)
            await asyncio.sleep(5)
    finally:
        for process in processes.values():
            await asyncio.to_thread(stop_worker_tree, process)
