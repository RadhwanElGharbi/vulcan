import sys,json,hashlib,struct,logging,time
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'.runtime/hwsd-parser'))
from access_parser import AccessParser
logging.basicConfig(level=logging.WARNING)
folder=Path('.runtime/hwsd-investigation')
start=time.monotonic();db=AccessParser(str(folder/'HWSD2.mdb'))
print('loaded',round(time.monotonic()-start,2),db.catalog,flush=True)
proof=json.loads((folder/'conversion-verification.json').read_text())
results=[]
for expected in proof['tables']:
    name=expected['table'];start=time.monotonic();table=db.parse_table(name)
    fields=sorted(table);lengths={len(v) for v in table.values()}
    counts=Counter()
    if len(lengths)!=1:raise ValueError((name,lengths))
    for row in zip(*(table[f] for f in fields)):
        h=hashlib.sha256()
        for value in row:
            if value is None:h.update(b'\x00')
            elif isinstance(value,str):
                raw=value.encode('utf-8');h.update(b'\x02'+struct.pack('<I',len(raw))+raw)
            else:h.update(b'\x01'+struct.pack('<d',value))
        counts[h.hexdigest()]+=1
    actual=hashlib.sha256(''.join(k+'\t'+str(v)+'\n' for k,v in sorted(counts.items())).encode()).hexdigest()
    result={'table':name,'rows':sum(counts.values()),'sha256':actual,'equal':actual==expected['mdb_content_sha256'],'seconds':round(time.monotonic()-start,2)}
    results.append(result);print(json.dumps(result),flush=True)
    del table
(folder/'native-parser-verification.json').write_text(json.dumps({'equal':all(r['equal'] for r in results),'results':results},indent=2))
