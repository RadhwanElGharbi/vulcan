"""Independent, explicitly scoped CF baseline checks; run in the reference runtime.

The checker runs in a separate environment. Its CF 1.11 suite cannot certify CF
1.12: only its exact-version-string check is skipped, with this limitation saved
alongside every report. Published generations are read-only inputs.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'gui-v2/backend'))
from api.dataset_fetch.research.contracts import PlannedSelection, file_hash, now
from api.dataset_fetch.research.registry import registry
from api.dataset_fetch.research.climatology import export_climatology
from api.dataset_fetch.research.climate import VARIABLES, validate_climate
from api.dataset_fetch.research.climate_spatial import export_climate
from api.dataset_fetch.research.store import atomic_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--checker-python',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    checker=args.checker_python.parent/'Scripts/cchecker.py'
    env={**os.environ,'PATH':str(args.checker_python.parent/'Library/bin')+os.pathsep+os.environ['PATH']}
    products=registry()
    cases=[]
    index=json.loads((ROOT/'qa/research-qualification-index.json').read_text())
    for record in index['products']:
        if not record['product'].startswith('worldclim-'):continue
        attempt=record['latest_acquisition_attempt']
        if attempt['status']!='passed':raise ValueError('WorldClim live input is unavailable')
        base=(ROOT/attempt['evidence_file']).parent/'data/generations'/attempt['job_id']
        sources=[p for p in base.glob('*/*-0000.tif') if p.is_file()]
        if len(sources)!=1:raise ValueError('Ambiguous retained WorldClim output inventory')
        source=sources[0];mask=source.with_suffix('.gaps.tif')
        out=args.output/record['product'];out.mkdir()
        first=export_climatology(source,mask,out/'export.nc',products[record['product']],file_hash(source))
        second=export_climatology(source,mask,out/'repeat.nc',products[record['product']],file_hash(source))
        if first['scientific_hash']!=second['scientific_hash']:raise ValueError('Climatology replay differs')
        cases.append({'product':record['product'],'input_kind':'preserved live acquisition','input':str(source),'input_sha256':file_hash(source),'mask_sha256':file_hash(mask),
                      'export':str(out/'export.nc'),'scientific_hash':first['scientific_hash'],'identical_repeat':True})
    import xarray as xr
    aoi={'type':'Polygon','coordinates':[[[0,0],[.5,0],[.5,.5],[0,.5],[0,0]]]}
    for requested,(name,units) in VARIABLES.items():
        out=args.output/requested;out.mkdir()
        data=np.arange(96,dtype='float32').reshape(24,2,2)/100+(273.15 if requested=='2m_temperature' else 1)
        attrs={'units':units,'GRIB_stepType':'accum' if name=='tp' else 'instant','long_name':requested.replace('_',' ')}
        fixture=xr.Dataset({name:(('time','latitude','longitude'),data,attrs)},coords={
            'time':('time',np.arange(24),{'units':'hours since 2020-01-01 00:00:00','calendar':'proleptic_gregorian'}),
            'latitude':('latitude',[.375,.125],{'units':'degrees_north'}),'longitude':('longitude',[.125,.375],{'units':'degrees_east'})})
        source=out/'source.nc';fixture.to_netcdf(source,engine='netcdf4')
        selection=PlannedSelection(id='climate',product=products['era5-single-levels'],parameters={'variables':[requested]},assets=[],recipe={})
        validate_climate(source,[requested],'2020-01-01')
        export_climate(source,out/'export.nc',selection,SimpleNamespace(aoi=aoi),'2020-01-01')
        cases.append({'product':'era5-single-levels','variable':requested,'input_kind':'immutable synthetic fixture; live provider not verified',
                      'input':str(source),'input_sha256':file_hash(source),'export':str(out/'export.nc')})
    for case in cases:
        export=Path(case['export']);report=export.with_name('checker.json')
        command=[str(args.checker_python),str(checker),'--test','cf:1.11','--criteria','strict','--skip-checks','check_conventions_version','-f','json_new','-o',str(report),str(export)]
        process=subprocess.run(command,env=env,text=True,capture_output=True,timeout=180)
        export.with_name('checker.log').write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
        case.update({'export_sha256':file_hash(export),'report':str(report),'checker_exit_code':process.returncode,'report_sha256':file_hash(report) if report.exists() else None})
        print(case['product'],case.get('variable',''),process.returncode,flush=True)
    summary={'checked_at':now(),'checker':'IOOS compliance-checker 6.1.0, separate environment','suite':'cf:1.11','criteria':'strict',
             'skipped_checks':{'check_conventions_version':'Exports declare CF-1.12, which this checker does not support. All other CF 1.11 checks run.'},
             'full_cf_1_12_qualification':'pending','cases':cases,'baseline_passed':all(c['checker_exit_code']==0 for c in cases)}
    atomic_json(args.output/'summary.json',summary)
    if not summary['baseline_passed']:raise SystemExit(1)


if __name__=='__main__':main()
