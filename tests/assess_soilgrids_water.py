"""Retain official evidence for unimplemented coarse SoilGrids water products."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/api'))
from api.dataset_fetch.research.contracts import now
from api.dataset_fetch.research.store import Store,atomic_json
from api.dataset_fetch.research.transport import Transport

URLS=[
    'https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html',
    'https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html',
    'https://isric.org/news/coarse-resolution-soilgrids-property-maps-released-1000-m-and-5000-m',
]

if __name__=='__main__':
    store=Store();client=Transport(store);evidence=[]
    for url in URLS:
        raw=bytearray()
        with client.request('GET',url,stream=True,deadline=45) as response:
            for block in client.chunks(response):
                raw.extend(block)
                if len(raw)>4*1024**2:raise ValueError('Source evidence exceeds 4 MiB')
            evidence.append(client.retain_bytes(bytes(raw),url,response.headers).model_dump(mode='json'))
    inspection=json.loads((ROOT/'.runtime/soil-water-investigation.json').read_bytes())
    for item in inspection.values():
        store.verify_blob(item['checksum_receipt']['sha256'])
        if item.get('receipt'):store.verify_blob(item['receipt']['sha256'])
    record={'checked_at':now(),'status':'eligible_interface_identified_implementation_pending',
        'scope':'Discovery, documentation and one downloaded header/checksum inspection only; not validated acquisition or product qualification',
        'documentation':evidence,'inspection':inspection,
        'findings':[
            'Public 1000 m mean files and publisher SHA-256 inventories exist for wv0010, wv0033 and wv1500 at all six standard depths.',
            'Official documentation describes coarser aggregation of 250 m mean predictions; no coarser uncertainty product is provided in these inventories.',
            'Mapped units are 10^-3 cm3/cm3. Preserve original integer values and declare the encoding; division by ten yields percent.',
            'The FAQ spells the 33 kPa property wv003; actual downloadable filenames and inventories use wv0033. Do not silently guess another URL.',
            'A February 2022 release announcement and June 2022 file dates do not establish local observation dates or exact source survey vintages.',
            'Detailed aggregation weights, NoData/boundary treatment and per-cell uncertainty are not established by the retained overview documentation.',
            'Keep separate product identities from 250 m SoilGrids and HWSD; no substitution when either fails.'
        ]}
    atomic_json(ROOT/'tests/soilgrids-water-assessment.json',record)
    print(json.dumps({'status':record['status'],'documents':len(evidence),'properties':sorted(inspection)}))
