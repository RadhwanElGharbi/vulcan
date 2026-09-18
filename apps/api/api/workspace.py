"""Local workspace selection; all project readers continue using the standard layout."""
import os
import threading
import subprocess
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from .project_utils import workspace_settings, set_projects_directory, get_projects_root

router = APIRouter()
_PICKER_LOCK = threading.Lock()


@router.post('/workspace/pick-directory')
def open_directory_picker():
    from .native_folder_picker import pick_directory
    if not _PICKER_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'A folder picker is already open. Finish or cancel it first.')
    try:
        directory = pick_directory(get_projects_root())
        return {'directory': directory, 'cancelled': directory is None}
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(408, 'Folder selection timed out. Choose directory to try again.') from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        _PICKER_LOCK.release()


class DirectoryChoice(BaseModel):
    directory: str


@router.get('/workspace')
def get_workspace(request: Request):
    return {**workspace_settings(), **getattr(request.state, 'cloud_session', {})}


@router.put('/workspace')
def choose_workspace(choice: DirectoryChoice):
    try:
        return set_projects_directory(choice.directory)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/workspace/directories')
def browse_directories(path: str | None = None):
    directory = Path(path).expanduser() if path else get_projects_root()
    if not directory.is_absolute():
        raise HTTPException(400, 'Enter an absolute directory path.')
    try:
        directory = directory.resolve(strict=True)
        if not directory.is_dir():
            raise ValueError('This path is not a directory.')
        children = sorted((child for child in directory.iterdir() if child.is_dir() and not child.name.startswith('.')), key=lambda child: child.name.casefold())
        roots = [str(Path.home())]
        if os.name == 'nt':
            import ctypes
            drives = ctypes.windll.kernel32.GetLogicalDrives()
            roots.extend(f'{chr(65+i)}:\\' for i in range(26) if drives & (1 << i))
        else:
            roots.append('/')
        return {'directory': str(directory), 'parent': str(directory.parent) if directory.parent != directory else None,
                'shortcuts': list(dict.fromkeys([*roots, *workspace_settings()['directories']])),
                'folders': [{'name': child.name, 'path': str(child)} for child in children[:500]],
                'truncated': len(children) > 500}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, f'Cannot open directory: {exc}') from exc
