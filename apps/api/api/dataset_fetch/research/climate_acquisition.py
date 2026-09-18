"""Small independently checkpointed CDS requests; no implicit multi-variable merge."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date,timedelta
from pathlib import Path
from .contracts import AcquisitionReceipt,canonical,now
from .store import atomic_json


def cds_requests(product,parameters,boxes):
    if len(boxes)!=1:
        raise ValueError('CDS requests require one contiguous AOI bounding box')
    w,s,e,n=boxes[0]
    start,end=date.fromisoformat(parameters['start']),date.fromisoformat(parameters['end'])
    requests=[]
    for offset in range((end-start).days+1):
        day=start+timedelta(days=offset)
        for variable in sorted(parameters['variables']):
            requests.append({'id':f'climate:{day.isoformat()}:{variable}','date':day.isoformat(),'variable':variable,
                'dataset':product.semantics['dataset'],
                'request':{'product_type':['reanalysis'],'variable':[variable],'year':[str(day.year)],'month':[f'{day.month:02d}'],
                    'day':[f'{day.day:02d}'],'time':[f'{hour:02d}:00' for hour in range(24)],'area':[n,w,s,e],
                    'grid':product.semantics['provider_grid'],'data_format':'netcdf','download_format':'unarchived'}})
    if not requests or len(requests)*24>10000:
        raise ValueError('Climate request is empty or exceeds 10,000 variable/time steps')
    return requests


def acquire_cds(selection,transport,checkpoint,save_checkpoint):
    descriptor=next(d['cds'] for d in selection.discovery if 'cds' in d)
    requests=cds_requests(selection.product,selection.parameters,descriptor['bbox'])
    if requests!=descriptor['requests']:
        raise ValueError('CDS execution requests differ from the frozen request inventory')
    receipts=[]
    for item in requests:
        key=selection.id+':'+item['id']
        if key in checkpoint:
            rows=[AcquisitionReceipt.model_validate(row) for row in checkpoint[key]]
            if {row.asset_id for row in rows}!={item['id'],'cds-request:'+item['id'],'cds-metadata:'+item['id']}:
                raise ValueError('CDS checkpoint has an incomplete request or response inventory')
            for row in rows: transport.store.verify_blob(row.sha256)
        else:
            rows=retrieve_one(item,selection.product.endpoint,transport)
            checkpoint[key]=[row.model_dump(mode='json') for row in rows]
            save_checkpoint(checkpoint)
        receipts.extend(rows)
    return receipts


def retrieve_one(item,endpoint,transport):
    with tempfile.TemporaryDirectory(dir=transport.store.root) as directory:
        directory=Path(directory)
        request_file,output,metadata=directory/'request.json',directory/'response.nc',directory/'metadata.json'
        atomic_json(request_file,{'parent_pid':os.getpid(),'dataset':item['dataset'],'request':item['request'],'output':str(output),'metadata':str(metadata),'max_bytes':transport.remaining_bytes})
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        process=subprocess.Popen([sys.executable,'-m','api.dataset_fetch.research.climate_acquisition',str(request_file)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
        started=time.monotonic()
        try:
            while process.poll() is None:
                transport.check()
                if time.monotonic()-started>3600: raise ValueError('CDS task exceeded its one-hour deadline')
                if output.exists() and output.stat().st_size>transport.remaining_bytes: raise ValueError('CDS output exceeded the approved storage budget')
                time.sleep(.2)
            if process.returncode or not output.is_file() or not metadata.is_file():
                raise ValueError('CDS request failed; credentials, provider terms, availability, quota or response validation require review')
            value=json.loads(metadata.read_text(encoding='utf-8'))
            if value['content_length']!=output.stat().st_size:
                raise ValueError('CDS response length contradicts its provider result metadata')
            request_receipt=transport.retain_bytes(canonical(item),endpoint).model_copy(update={'asset_id':'cds-request:'+item['id']})
            metadata_receipt=transport.retain_bytes(metadata.read_bytes(),endpoint).model_copy(update={'asset_id':'cds-metadata:'+item['id']})
            sha,retained=transport.store.retain(output)
            response=transport.account(AcquisitionReceipt(asset_id=item['id'],source_url=endpoint,sha256=sha,size=retained.stat().st_size,
                acquired_at=now(),headers={'content-type':value['content_type']},blob=sha))
            return [request_receipt,metadata_receipt,response]
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)


def child_main():
    import cdsapi
    from .http_transfer import watch_parent
    from .transport import safe_url
    from .contracts import file_hash
    task=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    watch_parent(task['parent_pid'])
    client=cdsapi.Client(url='https://cds.climate.copernicus.eu/api',key=os.environ['CDSAPI_KEY'],quiet=True,debug=False,timeout=60,retry_max=3)
    result=client.retrieve(task['dataset'],task['request'])
    if not 0<result.content_length<=task['max_bytes']:
        raise ValueError('CDS result exceeds the approved byte limit')
    result.download(task['output'])
    asset=dict(result.asset)
    # Authorization links are not scientific metadata. Preserve their unsigned
    # object identity and explicitly identify the redaction.
    if asset.get('href'): asset['href']=safe_url(result.location).split('?')[0]
    checksum=asset.get('file:checksum')
    if checksum:
        if str(checksum).startswith('1220') and len(checksum)==68: algorithm,expected='sha256',checksum[4:]
        elif ':' in checksum: algorithm,expected=checksum.split(':',1)
        else: raise ValueError('CDS supplied an uninterpretable mandatory checksum')
        if file_hash(Path(task['output']),algorithm)!=expected:
            raise ValueError('CDS publisher checksum mismatch')
    atomic_json(Path(task['metadata']),{'asset':asset,'content_length':result.content_length,'content_type':result.content_type,
        'redactions':['Temporary asset authorization query removed; permanent object path retained']})


if __name__=='__main__': child_main()
