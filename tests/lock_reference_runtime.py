"""Capture the installed Windows reference environment, never a floating solve.

Run with --write after validation; the default compares the current environment.
Package URLs/hashes supplement (and do not replace) each plan's runtime fingerprint.
"""
import argparse
import importlib.metadata as metadata
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api'))
from api.dataset_fetch.research.planning import runtime_fingerprint
from api.dataset_fetch.research.contracts import file_hash, now
from api.dataset_fetch.research.store import atomic_json


def capture():
    packages = []
    for path in sorted((Path(sys.prefix) / 'conda-meta').glob('*.json')):
        record = json.loads(path.read_text())
        packages.append({key: record[key] for key in ('name', 'version', 'build', 'url', 'sha256', 'md5')})
    if not packages:
        raise ValueError('The reference environment must include its conda package records')
    pip = sorted(({'name': d.metadata['Name'], 'version': d.version} for d in metadata.distributions()
                  if (d.read_text('INSTALLER') or '').strip() == 'pip'), key=lambda p: p['name'].lower())
    runtime = runtime_fingerprint()
    # Source identity belongs to each frozen plan, not the reusable environment.
    runtime.pop('implementation_files')
    runtime.pop('protocol_implementation')
    return {'schema_version': 'zeus.reference-runtime/1', 'runtime': runtime, 'conda_packages': packages, 'pip_packages': pip}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'docs/datasets/reference-runtime'
    lock = directory / 'lock.json'
    current = capture()
    if not args.write:
        expected = json.loads(lock.read_text())
        if any(expected[key] != current[key] for key in current):
            raise SystemExit('Reference runtime mismatch; replay is not qualified on this environment')
        print('Reference runtime matches the recorded package builds and transformation resources')
        return
    directory.mkdir(parents=True, exist_ok=True)
    conda = '@EXPLICIT\n' + '\n'.join(p['url'] + '#' + p['md5'] for p in current['conda_packages']) + '\n'
    (directory / 'conda-win-64.txt').write_text(conda, encoding='utf-8')
    pins = '\n'.join(p['name'] + '==' + p['version'] for p in current['pip_packages']) + '\n'
    pin_path = directory / 'pip-win-64.txt'
    pin_path.write_text(pins, encoding='utf-8')
    archive = ROOT / '.runtime/reference-packages'
    archive.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps', '--dest', str(archive), '-r', str(pin_path)], check=True)
    from packaging.utils import parse_wheel_filename, parse_sdist_filename, canonicalize_name
    files = {}
    for path in archive.iterdir():
        if path.suffix == '.whl':
            name, version, *_ = parse_wheel_filename(path.name)
        else:
            name, version = parse_sdist_filename(path.name)
        files[(canonicalize_name(name), str(version))] = {'filename': path.name, 'sha256': file_hash(path), 'size': path.stat().st_size}
    records = []
    for p in current['pip_packages']:
        record = files[(canonicalize_name(p['name']), p['version'])]
        records.append(p['name']+'=='+p['version']+' --hash=sha256:'+record['sha256'])
    pin_path.write_text('\n'.join(records)+'\n', encoding='utf-8')
    current['pip_archives'] = [files[(canonicalize_name(p['name']), p['version'])] for p in current['pip_packages']]
    current['captured_at'] = now()
    atomic_json(lock, current)
    print('Captured', len(current['conda_packages']), 'conda packages and', len(records), 'pip archives')


if __name__ == '__main__':
    main()
