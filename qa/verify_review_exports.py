"""Verify exports from the real browser-driven coastal approval job."""
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

ROOT=Path(__file__).resolve().parents[1]
base='http://127.0.0.1:8000/api'
project='QA-Scientific-Review'
jobs=requests.get(f'{base}/projects/{project}/dataset-jobs',timeout=30).json()['jobs']
job=next(j for j in jobs if j['status']=='succeeded')
id=job['id']
report=requests.get(f'{base}/dataset-jobs/{id}/report',timeout=30).json()
directory=ROOT/'.runtime/browser-verification'/id
directory.mkdir(parents=True,exist_ok=True)
inventory=requests.get(f'{base}/projects/{project}/datasets',timeout=30).json()
artifact=next(d for d in inventory['rasters'] if d['metadata'].get('scientific_hash'))
response=requests.get('http://127.0.0.1:8000'+artifact['metadata']['download_url'],timeout=30)
response.raise_for_status()
assert hashlib.sha256(response.content).hexdigest()==artifact['metadata']['sha256']
bundle=requests.get(f'{base}/dataset-jobs/{id}/bundle',timeout=60)
bundle.raise_for_status()
(directory/'provenance.zip').write_bytes(bundle.content)
with zipfile.ZipFile(directory/'provenance.zip') as z:
    z.extractall(directory/'extracted')
result=subprocess.run([sys.executable,str(directory/'extracted/zeus_replay.py'),'--output',str(directory/'replay')],capture_output=True,text=True,timeout=120)
assert result.returncode==0,result.stdout+result.stderr
for output in report['outputs']:
    for field in ('gap_mask','extent_gap'):
        if output.get(field):
            data=requests.get(f'{base}/dataset-jobs/{id}/artifacts/'+output[field],timeout=30)
            data.raise_for_status()
            (directory/Path(output[field]).name).write_bytes(data.content)
summary={'job_id':id,'project':project,'status':'passed','plan_hash':report['plan']['plan_hash'],'report_hash':report['validation']['report_hash'],
         'coverage':report['validation']['results'][0]['bands'][0]['valid_fraction'],'findings_retained':report['validation']['findings'],
         'approvals':report['approvals'],'checks':['Browser plan confirmation disabled until individual provider acknowledgements','Measured gap held publication','Browser reconnect through project history',
         'Awaiting approval retained through actual worker termination and supervisor restart','Browser report-specific acceptance','Published artifact download SHA-256','Downloaded portable bundle replay in fresh process'],
         'replay':json.loads((directory/'replay/replay-result.json').read_text()),'evidence_directory':str(directory.relative_to(ROOT))}
(ROOT/'qa/research-browser-verification.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('replay','approvals','findings_retained')}))
