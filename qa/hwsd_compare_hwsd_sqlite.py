import sys,sqlite3,json,hashlib,struct
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'gui-v2/backend'))
from api.dataset_fetch.research.store import Store,atomic_json
from api.dataset_fetch.research.contracts import file_hash,now
folder=Path('.runtime/hwsd-investigation')
records=json.loads((folder/'inputs.json').read_text())
path=Store().verify_blob(records['HWSD2.sqlite']['receipt']['sha256'])
original={};columns={};types={};name=None
for line in (folder/'mdb-row-hashes.txt').read_text().splitlines():
    if line.startswith('TABLE\t'):
        _,name,fields=line.split('\t');columns[name]=fields.split('|');original[name]=Counter()
    elif line.startswith('TYPES\t'):types[name]=line.split('\t')[1].split('|')
    else: original[name][line]+=1
results=[]
with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
    for name in sorted(original):
        fields=columns[name]
        native=sorted(r[1] for r in db.execute('PRAGMA table_info('+name+')'))
        if native!=fields:raise ValueError((name,native,fields))
        actual=Counter()
        for row in db.execute('SELECT '+','.join('"'+f+'"' for f in fields)+' FROM '+name):
            h=hashlib.sha256()
            for datatype,value in zip(types[name],row):
                if value is None:h.update(b'\x00')
                elif isinstance(value,str):
                    value=value.encode('utf-8');h.update(b'\x02'+struct.pack('<I',len(value))+value)
                else:
                    if datatype=='Single':value=struct.unpack('<f',struct.pack('<f',value))[0]
                    h.update(b'\x01'+struct.pack('<d',value))
            actual[h.hexdigest()]+=1
        missing=original[name]-actual;extra=actual-original[name]
        def aggregate(rows):return hashlib.sha256(''.join(k+'\t'+str(v)+'\n' for k,v in sorted(rows.items())).encode()).hexdigest()
        result={'table':name,'columns':fields,'native_types':types[name],'mdb_rows':sum(original[name].values()),'sqlite_rows':sum(actual.values()),
                'mdb_content_sha256':aggregate(original[name]),'sqlite_content_sha256':aggregate(actual),
                'missing_or_changed_rows':sum(missing.values()),'extra_or_changed_rows':sum(extra.values()),'equal':actual==original[name]}
        results.append(result);print(json.dumps(result),flush=True)
    extras=sorted({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}-set(original))
atomic_json(folder/'conversion-verification.json',{'checked_at':now(),'method':'Full unordered row multiset, exact UTF-8 strings/nulls/native-typed numbers, alphabetically ordered columns; SQLite numbers restored to MDB Single precision before exact binary equality; every original MDB user table',
    'inputs':records,'tables':results,'additional_sqlite_tables':extras,'equal':all(r['equal'] for r in results),
    'mdb_reader':'Host Microsoft Access ODBC via System.Data.Odbc; independent qualification tool, not a replay dependency',
    'scripts':{n:file_hash(Path('.runtime')/n) for n in ['compare_hwsd_mdb.ps1','compare_hwsd_sqlite.py']}})
