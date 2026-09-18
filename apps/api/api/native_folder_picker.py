"""Native folder selection on the local desktop, isolated from the API process."""
import json
import os
import subprocess
import sys
from pathlib import Path


def pick_directory(initial: Path) -> str | None:
    if os.name == 'nt':
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-STA', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
             '-File', str(Path(__file__).with_suffix('.ps1'))],
            env={**os.environ, 'VULCAN_PICKER_INITIAL': str(initial)},
            capture_output=True, encoding='utf-8-sig', errors='replace', timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Could not open the Windows folder picker.')
        choice = json.loads(result.stdout)
        return None if choice['cancelled'] else choice['directory']
    if sys.platform == 'darwin':
        script = 'on run argv\nreturn POSIX path of (choose folder with prompt "Choose project directory" default location (POSIX file (item 1 of argv)))\nend run'
        result = subprocess.run(['osascript', '-e', script, str(initial)], capture_output=True, text=True, timeout=300)
        if result.returncode and '(-128)' in result.stderr:
            return None
    else:
        result = subprocess.run(['zenity', '--file-selection', '--directory', '--title=Choose project directory',
                                 f'--filename={initial}{os.sep}'], capture_output=True, text=True, timeout=300)
        if result.returncode == 1:
            return None
    if result.returncode:
        raise RuntimeError('Could not open the system folder picker.')
    return result.stdout.strip() or None
