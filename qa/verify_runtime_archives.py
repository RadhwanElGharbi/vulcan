"""Verify reconstructed conda archives against the SHA-256 reference lock.

Conda's explicit format supplies MD5; micromamba consequently omits SHA-256
from installed package records. --record adds only the independently computed,
lock-verified SHA-256 to those records for the portable runtime fingerprint.
"""
import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        while block:=source.read(1024*1024):h.update(block)
    return h.hexdigest()


def atomic(path,value):
    fd,name=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def verify(prefix,cache,record=False):
    lock=json.loads((ROOT/'docs/datasets/reference-runtime/lock.json').read_text())
    checked=[];updates=[]
    for package in lock['conda_packages']:
        archive=cache/Path(urlsplit(package['url']).path).name
        if not archive.is_file() or sha256(archive)!=package['sha256']:
            raise ValueError('Missing or mismatched reference package archive: '+archive.name)
        path=prefix/'conda-meta'/f"{package['name']}-{package['version']}-{package['build']}.json"
        installed=json.loads(path.read_text())
        if any(installed.get(key)!=package[key] for key in ('name','version','build','md5')):
            raise ValueError('Installed package identity differs from the verified archive: '+package['name'])
        if installed.get('sha256') and installed['sha256']!=package['sha256']:
            raise ValueError('Installed SHA-256 conflicts with the verified archive')
        checked.append({'name':package['name'],'version':package['version'],'build':package['build'],'archive':archive.name,'sha256':package['sha256'],'url':package['url']})
        if not installed.get('sha256'):
            installed['sha256']=package['sha256']
            installed['zeus_sha256_basis']='Computed from retained package archive and matched against the reference SHA-256 lock'
            updates.append((path,installed))
    if record:
        for path,value in updates:atomic(path,value)
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'prefix':str(prefix),'archives_verified':len(checked),'sha256_records_added':len(updates) if record else 0,'packages':checked}
    atomic(ROOT/'qa/reference-recreation-artifacts.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',type=Path,required=True);parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--record',action='store_true')
    args=parser.parse_args();result=verify(args.prefix,args.cache,args.record)
    print({key:value for key,value in result.items() if key!='packages'})
