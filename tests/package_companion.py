"""Build the public companion from an explicit source allowlist; never include user data."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
files = [ROOT/'start-companion.ps1', ROOT/'start-companion.cmd', ROOT/'tests/verify_runtime_archives.py']
files += [p for p in (ROOT/'apps/api').rglob('*') if p.is_file() and p.suffix in {'.py','.ps1','.txt','.json'} and '__pycache__' not in p.parts]
for name in ['reference-runtime/conda-win-64.txt','reference-runtime/pip-win-64.txt','reference-runtime/lock.json',
             'WORLD_DATASET_CATALOGUE.csv','catalogue-assessment.json','DATASET_FETCHING_PROTOCOLS.md','providers.json']:
    path = ROOT/'docs/datasets'/name
    if path.exists(): files.append(path)
target = ROOT/'apps/web/public/downloads/vulcan-companion-windows.zip'
target.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        archive.write(path, 'Vulcan/'+path.relative_to(ROOT).as_posix())
    archive.writestr('Vulcan/README.txt', 'VULCAN local companion (Windows 64-bit)\n\nExtract the whole ZIP. Double-click start-companion.cmd.\nThe first run installs and verifies the pinned runtime. Internet access and disk space are required.\nOpen https://vulcan.colony.tech and choose Connect local companion.\nAllow local network access for this site if your browser requests it.\nProjects default to Documents/Vulcan/Projects. Change the location in Project Index.\nRuntime, cache and job ledger are stored under %LOCALAPPDATA%/Vulcan.\nThis package contains companion source and dependency locks; no user projects or credentials.\n')
sha = hashlib.sha256(target.read_bytes()).hexdigest()
target.with_suffix('.zip.sha256').write_text(sha+'  '+target.name+'\n', encoding='utf-8')
print(json.dumps({'file':str(target),'bytes':target.stat().st_size,'files':len(files),'sha256':sha}))
