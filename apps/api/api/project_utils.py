"""
Project discovery helpers.

Projects default to the configured local directory (overridable via AGRS_PROJECTS_ROOT)
and are considered valid if they contain project_metadata.json.
The project name prefers the value inside project_metadata.json when present;
otherwise the directory name is used.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Optional

PROJECTS_ROOT_ENV_VAR = "AGRS_PROJECTS_ROOT"
DEFAULT_PROJECTS_ROOT = Path(__file__).resolve().parents[3] / "Projects"
# Keep a module-level constant for backwards compatibility with existing imports.
PROJECTS_ROOT = Path(os.getenv(PROJECTS_ROOT_ENV_VAR, str(DEFAULT_PROJECTS_ROOT)))

_DISCOVERY_CACHE_TTL_SECONDS = max(
    0.0,
    float(os.getenv("AGRS_PROJECT_DISCOVERY_CACHE_TTL_SECONDS", "15")),
)
_DISCOVERY_CACHE_LOCK = threading.Lock()
_DISCOVERY_CACHE_ROOT: Optional[tuple[Path, ...]] = None
_DISCOVERY_CACHE_LOADED_AT: float = 0.0
_DISCOVERY_CACHE_PROJECTS: Dict[str, Path] = {}


def workspace_settings_path() -> Path:
    return Path(os.getenv('ZEUS_WORKSPACE_SETTINGS', str(DEFAULT_PROJECTS_ROOT.parent / '.runtime' / 'workspace.json')))


def workspace_settings() -> dict:
    """Read on every call so API and dedicated worker share persisted locations."""
    from .cloud_workspace import workspace_path
    cloud = workspace_path()
    if cloud is not None:
        directory = str(cloud / 'Projects')
        return {'version': 1, 'directory': directory, 'directories': [directory], 'mode': 'temporary_cloud'}
    default = str(Path(os.getenv(PROJECTS_ROOT_ENV_VAR, str(DEFAULT_PROJECTS_ROOT))).resolve())
    path = workspace_settings_path()
    if not path.exists():
        return {'version': 1, 'directory': default, 'directories': [default]}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('directories'), list) or not data.get('directory'):
        raise ValueError('Invalid workspace settings; refusing to use a different save location.')
    directories = list(dict.fromkeys([default, *data['directories'], data['directory']]))
    if not all(isinstance(value, str) and Path(value).is_absolute() for value in directories):
        raise ValueError('Workspace directories must be absolute paths.')
    return {**data, 'directories': directories}


def get_projects_root() -> Path:
    return Path(workspace_settings()['directory'])


def _scan_projects(roots) -> Dict[str, Path]:
    discovered: Dict[str, Path] = {}
    seen_dirs = set()
    for root in roots:
        if not root.is_dir():
            continue
        # Stop at a project boundary: retained provider metadata is not another project.
        for directory, children, files in os.walk(root, followlinks=False):
            children[:] = sorted(name for name in children if not name.startswith('.') and name not in {'node_modules', '__pycache__'})
            if 'project_metadata.json' not in files:
                continue
            children[:] = []
            project_dir = Path(directory).resolve()
            if project_dir in seen_dirs:
                continue
            seen_dirs.add(project_dir)
            metadata = load_json_file(project_dir / 'project_metadata.json')
            if not metadata:
                continue
            name = metadata.get('project_name') or project_dir.name
            if not isinstance(name, str) or not name.strip():
                continue
            if name in discovered and discovered[name] != project_dir:
                raise ValueError(f'Two projects are named "{name}". Rename one before adding this directory.')
            discovered[name] = project_dir
    return discovered


_WORKSPACE_WRITE_LOCK = threading.Lock()


def set_projects_directory(value: str) -> dict:
    directory = Path(value).expanduser()
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError('Choose an existing absolute directory on this computer.')
    directory = directory.resolve()
    if (directory / 'project_metadata.json').exists():
        raise ValueError('Choose the containing workspace folder, not an individual project folder.')
    with _WORKSPACE_WRITE_LOCK:
        data = workspace_settings()
        directories = list(dict.fromkeys([*data['directories'], str(directory)]))
        _scan_projects([Path(root) for root in directories])  # Reject ambiguous project names before changing settings.
        with tempfile.TemporaryFile(dir=directory):
            pass  # Verify that new projects can actually be saved here.
        data = {'version': 1, 'directory': str(directory), 'directories': directories}
        path = workspace_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        discover_project_paths(force_refresh=True)
        return data


def load_json_file(file_path: Path) -> Optional[dict]:
    """Load JSON with basic safety."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Error loading {file_path}: {exc}")
        return None


def _is_project_dir(directory: Path) -> bool:
    return (directory / "project_metadata.json").exists()


def discover_project_paths(force_refresh: bool = False) -> Dict[str, Path]:
    global _DISCOVERY_CACHE_ROOT, _DISCOVERY_CACHE_LOADED_AT, _DISCOVERY_CACHE_PROJECTS
    roots = tuple(Path(value) for value in workspace_settings()['directories'])
    now = time.monotonic()
    with _DISCOVERY_CACHE_LOCK:
        if not force_refresh and _DISCOVERY_CACHE_ROOT == roots and now - _DISCOVERY_CACHE_LOADED_AT <= _DISCOVERY_CACHE_TTL_SECONDS:
            return dict(_DISCOVERY_CACHE_PROJECTS)
    discovered = _scan_projects(roots)
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE_ROOT = roots
        _DISCOVERY_CACHE_LOADED_AT = now
        _DISCOVERY_CACHE_PROJECTS = discovered
    return dict(discovered)


def resolve_project_path(project_name: str) -> Optional[Path]:
    if not project_name or Path(project_name).name != project_name or project_name in {'.', '..'}:
        return None
    projects = discover_project_paths()
    resolved = projects.get(project_name)
    if resolved:
        return resolved
    return discover_project_paths(force_refresh=True).get(project_name)
