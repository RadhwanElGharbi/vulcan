"""Stage source for GitHub using an allowlist; never include local projects or credentials."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def selected_files():
    for name in ('README.md', '.gitignore', 'environment.yml', 'Start-Zeus.ps1', 'Start-Vulcan.ps1', 'Start VULCAN.cmd', 'LICENSE'):
        if (ROOT/name).is_file(): yield ROOT/name
    for folder, extensions in [
        ('gui-v2/backend', {'.py', '.ps1', '.txt'}),
        ('gui-v2/frontend/src', {'.ts', '.tsx', '.css'}),
        ('gui-v2/frontend/tools', {'.cjs'}),
        ('docs', {'.md', '.json', '.txt', '.csv'}),
        ('qa', {'.py', '.cjs'}),
        ('qa/fixtures', {'.md', '.xml', '.gml', '.prj', '.json'}),
        ('tools', {'.py', '.cjs'}),
    ]:
        for path in (ROOT/folder).rglob('*'):
            if path.is_file() and path.suffix in extensions and '__pycache__' not in path.parts:
                yield path
    for name in ('package.json', 'package-lock.json', 'next.config.js', 'next-env.d.ts', 'tsconfig.json',
                 'postcss.config.js', 'tailwind.config.ts', '.eslintrc.json', '.gitignore', '.vercelignore', 'vercel.json'):
        yield ROOT/'gui-v2/frontend'/name
    for path in (ROOT/'gui-v2/frontend/public').iterdir():
        if path.is_file() and path.suffix in {'.svg', '.png', '.ico'}: yield path


def prepare(destination):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('Use a new empty staging directory; never overwrite an existing checkout.')
    if not destination.is_relative_to((ROOT/'.runtime').resolve()):
        raise ValueError('Stage releases under this workspace .runtime directory.')
    records = {}
    forbidden = re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{30,}')
    files = sorted(set(selected_files()))
    for source in files:
        if source.is_symlink(): raise ValueError('Source symlink: '+str(source))
        if forbidden.search(source.read_text(encoding='utf-8', errors='replace')):
            raise ValueError('Possible credential; review before publishing: '+str(source.relative_to(ROOT)))
    for source in files:
        target = destination/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        records[source.relative_to(ROOT).as_posix()] = {'bytes': source.stat().st_size, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    # Operational QA JSONs and screenshots intentionally stay private. Fixture test code is included.
    (destination.parent/(destination.name+'-inventory.json')).write_text(json.dumps(records, indent=2))
    return {'directory': str(destination), 'files': len(records), 'bytes': sum(r['bytes'] for r in records.values()),
            'licence_present': (destination/'LICENSE').exists(), 'credential_pattern_scan': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    print(json.dumps(prepare(parser.parse_args().destination), indent=2))
