"""Revalidate current legacy artifact properties without claiming past provenance.

python -m api.dataset_fetch.research.legacy --project NAME --artifact data/.../file.tif --output EMPTY_DIRECTORY
"""
from __future__ import annotations
import argparse
import math
import os
import shutil
import tempfile
from pathlib import Path
from .contracts import Product,ProviderAssessment,Finding,digest,file_hash,now
from .planning import context,runtime_fingerprint
from .store import Store,atomic_json
from .validation import validate_raster,validate_vector


def revalidate(project,artifact,output,store=None):
    from osgeo import gdal,ogr
    gdal.UseExceptions();ogr.UseExceptions()
    gdal.SetConfigOption('PROJ_NETWORK','OFF')
    gdal.SetConfigOption('CPL_VSIL_CURL_ALLOWED_EXTENSIONS','.zeus-retained-inputs-only')
    ctx,aoi=context(project)
    root=ctx.project_path.resolve();source=(root/artifact).resolve()
    allowed=[root/'data/rasters/processed',root/'data/vectors/processed']
    if not any(source.is_relative_to(path) for path in allowed) or source.suffix.lower() not in ('.tif','.gpkg') or not source.is_file():
        raise ValueError('Revalidation requires an existing legacy TIFF or GeoPackage within the project processed-data directories')
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()): raise ValueError('Revalidation output directory must be empty')
    if output==source.parent or output.is_relative_to(source.parent): raise ValueError('Revalidation reports must be outside the legacy artifact directory')
    output.mkdir(parents=True,exist_ok=True)
    if source.suffix.lower()=='.gpkg' and any(Path(str(source)+suffix).exists() for suffix in ('-wal','-journal')):
        raise ValueError('Close the writable GeoPackage before creating a consistent revalidation snapshot')
    dependencies=[source]
    for candidate in [Path(str(source)+suffix) for suffix in ('.aux.xml','.msk','.ovr')]+[source.with_suffix(suffix) for suffix in ('.tfw','.wld','.prj')]:
        if candidate.is_file():
            if not candidate.resolve().is_relative_to(source.parent): raise ValueError('Legacy dependency resolves outside its artifact directory')
            dependencies.append(candidate)
    size=sum(path.stat().st_size for path in dependencies)
    if size>20*1024**3 or shutil.disk_usage(output).free<size+64*1024**2:
        raise ValueError('Legacy snapshot exceeds available storage or the 20 GiB artifact limit')
    store=store or Store();retained=output/'inputs';retained.mkdir()
    inventory=[]
    for path in dependencies:
        expected=file_hash(path)
        fd,name=tempfile.mkstemp(dir=store.root);os.close(fd)
        temporary=Path(name)
        try:
            shutil.copyfile(path,temporary)
            if file_hash(temporary)!=expected: raise ValueError('Legacy artifact changed while its snapshot was copied')
            sha,blob=store.retain(temporary)
            target=retained/path.name
            try: os.link(blob,target)
            except OSError: shutil.copyfile(blob,target)
            inventory.append({'file':path.name,'sha256':sha,'bytes':blob.stat().st_size,'role':'present legacy artifact; not an original acquisition input'})
        finally: temporary.unlink(missing_ok=True)
    path=retained/source.name
    findings=[Finding(id='legacy:history-unknown',rule='historical-provenance',severity='block',message='Original provider objects, discovery, acquisition identity and processing history are unavailable. Revalidation cannot establish or reconstruct them.')]
    results=[];properties={}
    try:
        if source.suffix.lower()=='.tif':
            from .national import check_signature
            with path.open('rb') as stream: check_signature(path.name,stream.read(16))
            dataset=gdal.Open(str(path))
            if dataset is None: raise ValueError('Legacy raster cannot be opened')
            properties={'dimensions':[dataset.RasterXSize,dataset.RasterYSize],'bands':[{'band':i,'datatype':gdal.GetDataTypeName(dataset.GetRasterBand(i).DataType),'units':dataset.GetRasterBand(i).GetUnitType() or None,
                         'scale':dataset.GetRasterBand(i).GetScale(),'offset':dataset.GetRasterBand(i).GetOffset()} for i in range(1,dataset.RasterCount+1)],'crs_wkt':dataset.GetProjection() or None}
            dataset=None
            product=Product(id='legacy-artifact',name=source.name,category='legacy',publisher='unknown',version=inventory[0]['sha256'],adapter='legacy_inventory',endpoint='zeus:legacy-artifact',units='unknown',kind='raster',attribution='unknown',assessment=ProviderAssessment(disposition='unavailable'))
            result,issues=validate_raster(path,aoi,product,selection_id='legacy-artifact',gap_path=output/'present-artifact-gaps.tif',legacy_inventory=True)
        else:
            dataset=ogr.Open(str(path))
            if dataset is None: raise ValueError('Legacy vector cannot be opened')
            properties={'layers':[{'name':layer.GetName(),'feature_count':layer.GetFeatureCount(),'crs_wkt':layer.GetSpatialRef().ExportToWkt() if layer.GetSpatialRef() else None} for layer in dataset]}
            dataset=None
            expected=sum(layer['feature_count'] for layer in properties['layers'])
            if expected<0 or expected>1_000_000: raise ValueError('Legacy vector inventory is unquantifiable or exceeds one million features')
            result,issues=validate_vector(path,aoi,selection_id='legacy-artifact',expected_count=expected,legacy_inventory=True)
        results.append(result);findings.extend(issues)
    except (ValueError,RuntimeError) as exc:
        findings.append(Finding(id='legacy:present-properties',rule='current-artifact-validation',severity='block',message=str(exc)))
    if any(file_hash(original)!=record['sha256'] for original,record in zip(dependencies,inventory)):
        raise ValueError('The legacy artifact changed during revalidation; no report can describe its current state')
    def finite_json(value):
        if isinstance(value,float) and not math.isfinite(value):return str(value)
        if isinstance(value,dict):return {key:finite_json(item) for key,item in value.items()}
        if isinstance(value,list):return [finite_json(item) for item in value]
        return value
    body={'schema_version':'zeus.legacy-revalidation/1','project':project,'artifact':str(source.relative_to(root)),'aoi_hash':digest(aoi),'runtime':runtime_fingerprint(),
          'inputs':inventory,'present_properties':finite_json(properties),'results':results,'findings':[f.model_dump(mode='json') for f in findings],
          'provenance_status':'legacy_unverified','publication_state':'not_published','original_acquisition_replay':'unavailable','acquisition_history':'unknown'}
    report={**body,'report_hash':digest(body),'checked_at':now()}
    atomic_json(output/'revalidation-report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True);parser.add_argument('--artifact',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=revalidate(args.project,args.artifact,args.output)
    print({'report':str(args.output/'revalidation-report.json'),'provenance_status':report['provenance_status'],'properties_scanned':bool(report['results']),'report_hash':report['report_hash']})
