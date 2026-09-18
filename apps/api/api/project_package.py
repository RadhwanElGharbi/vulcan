"""Bounded-memory project downloads including independently replayable acquisitions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .project_utils import resolve_project_path
from .dataset_fetch.research.store import Store
from .dataset_fetch.research.contracts import canonical, digest, now

router = APIRouter()
_EXPORT_LOCK = threading.Lock()
_ACTIVE = {'pending', 'running', 'awaiting_approval', 'cancelling', 'publishing'}


def project_files(root: Path) -> list[Path]:
    files = []
    for directory, children, names in os.walk(root, followlinks=False):
        # Uncommitted acquisitions and display caches are not project results.
        children[:] = sorted(n for n in children if n not in {'.acquisition', '__pycache__', '.cache'})
        for name in [*children, *names]:
            path = Path(directory) / name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('Project contains a link outside the portable project layout.')
        files.extend(Path(directory) / name for name in sorted(names) if (Path(directory) / name).is_file())
    return sorted(files)


def snapshot(root: Path):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in project_files(root)}


def project_jobs(store, project):
    jobs = sorted((j for j in store.jobs() if j['project'] == project), key=lambda j: j['id'])
    if any(j['status'] in _ACTIVE for j in jobs):
        raise ValueError('Finish, approve, or cancel the current fetch before downloading the project.')
    with store.connect() as connection:
        if connection.execute("SELECT 1 FROM discoveries WHERE project=? AND state IN ('pending','running','cancelling')", (project,)).fetchone():
            raise ValueError('Wait for source discovery to finish before downloading the project.')
    return jobs


def write_project_package(target: Path, root: Path, project: str, store: Store):
    from .dataset_fetch.research.bundle import write_bundle
    before = snapshot(root)
    jobs = project_jobs(store, project)
    # Resource bounds cover project files plus provenance bundles and ZIP assembly.
    maximum = int(os.getenv('VULCAN_PACKAGE_MAX_BYTES', str(20 * 1024**3)))
    total = sum(item[0] for item in before.values())
    estimate = total
    for job in jobs:
        if job['status'] != 'succeeded':
            continue
        plan = store.plan(job['plan_id'])
        hashes = {r['sha256'] for rows in job.get('receipts', {}).values() for r in rows}
        hashes.update(d['receipt']['sha256'] for selection in plan['selections'] for d in selection['discovery'] if 'receipt' in d)
        estimate += sum(store.blob_path(sha).stat().st_size for sha in hashes)
        base = root / 'data' / 'generations' / job['id']
        estimate += sum(p.stat().st_size for p in project_files(base))
    if estimate > maximum or shutil.disk_usage(target.parent).free < 2 * estimate + 64 * 1024**2:
        raise ValueError('Insufficient package capacity. Download individual datasets or increase export storage.')
    inventory = {}
    replay_jobs = []
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        def add(path, name):
            size = path.stat().st_size
            if sum(v['size'] for v in inventory.values()) + size > maximum:
                raise ValueError('The complete package exceeds the configured download limit.')
            sha = hashlib.sha256()
            count = 0
            with path.open('rb') as source, archive.open(name, 'w', force_zip64=True) as output:
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > size:
                        raise ValueError('Project changed while preparing the download. Try again.')
                    sha.update(chunk); output.write(chunk)
            if count != size:
                raise ValueError('Project changed while preparing the download. Try again.')
            inventory[name] = {'size': count, 'sha256': sha.hexdigest()}

        for relative in before:
            add(root / relative, 'project/' + Path(relative).as_posix())
        for job in jobs:
            # Preserve unsuccessful job evidence without presenting it as published data.
            audit = {'job': job, 'plan': store.plan(job['plan_id']), 'events': store.events(job['id'])}
            raw = canonical(audit)
            name = f"acquisitions/{job['id']}/audit.json"
            archive.writestr(name, raw)
            inventory[name] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            if job['status'] != 'succeeded':
                continue
            base = root / 'data' / 'generations' / job['id']
            if not base.is_dir():
                raise ValueError('A published acquisition generation is missing; a complete export is unavailable.')
            # write_bundle verifies artifact and preserved-input hashes, including the exact implementation.
            with tempfile.TemporaryDirectory(prefix='vulcan-replay-', dir=target.parent) as temporary:
                bundle = Path(temporary) / 'replay.zip'
                write_bundle(bundle, audit['plan'], job,
                             {'validation': job.get('report'), 'approvals': job.get('approvals', []), 'events': audit['events']},
                             store, base)
                add(bundle, f"acquisitions/{job['id']}/replay.zip")
            replay_jobs.append(job['id'])
        if before != snapshot(root) or digest(jobs) != digest(project_jobs(store, project)):
            raise ValueError('Project changed while preparing the download. Try again.')
        manifest = {'schema_version': 'vulcan.project-package/1', 'created_at': now(), 'project': project,
                    'replayable_jobs': replay_jobs, 'files': inventory,
                    'legacy_notice': 'Files without acquisition provenance remain legacy/unverified.',
                    'excluded': ['uncommitted acquisition staging', 'display caches']}
        archive.writestr('package-manifest.json', canonical(manifest))
        archive.writestr('README.txt',
            'VULCAN project package\n\nproject/ contains the project layout, AOI, metadata and retained results.\n'
            'acquisitions/ contains job evidence and a replay ZIP for each published acquisition.\n'
            'Extract a replay ZIP separately and follow its README to reproduce that acquisition offline.\n'
            'package-manifest.json records SHA-256 hashes for project and acquisition files.\n'
            'Legacy files are unverified. Downloading does not establish redistribution rights: retain source licences and attribution.\n'
            'Store this package safely: temporary hosted workspaces expire.\n')
    return manifest


@router.get('/projects/{project}/package')
def download_project_package(project: str):
    root = resolve_project_path(project)
    if root is None:
        raise HTTPException(404, 'Project not found.')
    if not _EXPORT_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Another project download is being prepared. Try again shortly.')
    path = None
    try:
        store = Store()
        fd, name = tempfile.mkstemp(prefix='vulcan-project-', suffix='.zip', dir=store.root)
        os.close(fd); path = Path(name)
        write_project_package(path, root, project, store)
        import re
        safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', project)
        return FileResponse(path, media_type='application/zip', filename=f'{safe_name}-vulcan.zip',
                            headers={'Cache-Control': 'private, no-store'},
                            background=BackgroundTask(path.unlink, missing_ok=True))
    except (ValueError, OSError, KeyError) as exc:
        if path: path.unlink(missing_ok=True)
        raise HTTPException(409, str(exc)) from exc
    finally:
        _EXPORT_LOCK.release()
