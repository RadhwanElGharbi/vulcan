"""Index all dated attempts, including failures superseded by later fixes."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'gui-v2/backend'))
from api.dataset_fetch.research.contracts import now
from api.dataset_fetch.research.registry import registry
from api.dataset_fetch.research.store import atomic_json

attempts=[]
for path in sorted((ROOT/'.runtime/qualification').glob('*/*/qualification.json')):
    value=json.loads(path.read_text(encoding='utf-8'))
    attempts.append({**{k:value[k] for k in ('product','checked_at','status','error','error_type','job_id','plan_hash','replay_one','replay_two','provenance_bundle') if k in value},
                     'evidence_file':str(path.relative_to(ROOT)), 'implementation':value.get('runtime',{}).get('protocol_implementation'),
                     'report_hash':value.get('report',{}).get('report_hash')})
latest={}
for item in sorted(attempts,key=lambda v:v['checked_at']): latest[item['product']]=item
products=[]
for product in registry().values():
    result=latest.get(product.id)
    products.append({'product':product.id,'latest_acquisition_attempt':result,'acquisition_status':result['status'] if result else 'not_executed',
                     'qualification':product.assessment.disposition,'qualification_complete':False})
atomic_json(ROOT/'qa/research-qualification-index.json',{'generated_at':now(),'scope':'Dated acquisition attempts; passing one AOI is not complete product qualification',
            'counts':{state:sum(v['acquisition_status']==state for v in products) for state in ('passed','failed','not_executed')},'products':products,'attempts':attempts})
print(json.dumps({'products':len(products),'attempts':len(attempts),'latest_passed':sum(v['acquisition_status']=='passed' for v in products)}))
