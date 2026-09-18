from __future__ import annotations

import json
import math
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .acquire import materialize, osm_features
from .contracts import canonical, digest, file_hash, scientific_recipe
from .national import archive_sources
from .store import atomic_json
from .transport import Cancelled
from .validation import validate_raster, validate_vector, validate_units


def run_selection(selection, receipts, plan, store, workdir, cancelled=lambda: False, *, prepared_inputs=None):
    from osgeo import gdal, ogr, osr
    gdal.UseExceptions()
    ogr.UseExceptions()
    gdal.SetConfigOption("PROJ_NETWORK", "OFF")
    gdal.SetConfigOption("GDAL_NUM_THREADS", "1")
    gdal.SetConfigOption('GDAL_VRT_ENABLE_PYTHON', 'NO')
    gdal.SetConfigOption('CPL_VSIL_CURL_ALLOWED_EXTENSIONS', '.zeus-retained-inputs-only')
    started = time.monotonic()
    def callback(progress, message, user_data):
        return 0 if cancelled() or time.monotonic()-started > 3600 else 1
    workdir.mkdir(parents=True, exist_ok=True)
    inputs = materialize(selection, receipts, store, workdir / "inputs") if prepared_inputs is None else prepared_inputs
    product = selection.product
    if product.adapter=='hwsd':
        from .hwsd import process
        return process(selection,inputs,plan,store,workdir,cancelled)
    if product.kind == "climate":
        from .climate import process_climate
        return process_climate(selection, receipts, plan, store, workdir, cancelled)
    if product.kind == "vector":
        from .vectors import process_vector as canonical_vectors
        return canonical_vectors(selection, receipts, inputs, plan, store, workdir, cancelled)
    sources = []
    for asset, path in inputs:
        if product.adapter=='os_terrain50':
            from .os_terrain import prepare
            sources.extend(prepare(asset,path,workdir,cancelled))
            continue
        if path.lower().endswith(".zip"):
            members=archive_sources(Path(path), product.adapter, cancelled)
            if product.adapter=='cgiar_srtm':
                import zipfile
                with zipfile.ZipFile(path) as archive:
                    if 'readme.txt' not in archive.namelist() or archive.getinfo('readme.txt').file_size>65536 or b'PROCESSED SRTM DATA VERSION 4.1' not in archive.read('readme.txt'):
                        raise ValueError('CGIAR archive does not establish the confirmed version 4.1 identity')
                if len(members)!=1 or not members[0].endswith('/'+asset.metadata['required_tiff']):
                    raise ValueError('CGIAR archive has an unexpected scientific tile inventory')
            sources.extend((asset, p) for p in members)
        else:
            sources.append((asset, path))
    if product.adapter == "soilgrids":
        from .soilgrids import validate_tiles
        validate_tiles(inputs,product)
        # The provider VRT is an index, not an extra resampling stage.
        # Its native precedence is encoded in frozen asset IDs.
        sources=[]
        for index,(asset,path) in enumerate(inputs):
            grid=asset.metadata['soilgrids_native_grid']
            window=grid['window']
            if window!=[0,0,grid['width'],grid['height']]:
                subset=workdir/f'native-soil-window-{index}.vrt'
                prepared=gdal.Translate(str(subset),path,format='VRT',srcWin=window)
                if prepared is None:raise ValueError('Could not preserve the native SoilGrids source window')
                prepared=None;path=str(subset)
            sources.append((asset,path))
    if not sources:
        raise ValueError("No raster source assets")
    if product.adapter == 'worldclim' and any(a.metadata.get('monthly_archive') for a,_ in sources):
        if len(sources) != 12:
            raise ValueError('WorldClim monthly archive does not contain exactly 12 rasters')
        import re
        months = [int(re.search(r'_(\d\d)\.tif$', path).group(1)) if re.search(r'_(\d\d)\.tif$', path) else None for _,path in sources]
        if months != list(range(1,13)):
            raise ValueError('WorldClim archive month identities are missing or unordered')
        signatures = set()
        for _,path in sources:
            source = gdal.Open(path)
            signatures.add((source.GetProjection(),source.GetGeoTransform(),source.RasterXSize,source.RasterYSize,source.RasterCount))
            source = None
        if len(signatures) != 1 or next(iter(signatures))[-1] != 1:
            raise ValueError('WorldClim monthly rasters have inconsistent native grids')
        combined = workdir / 'monthly-bands.vrt'
        vrt = gdal.BuildVRT(str(combined), [path for _,path in sources], separate=True)
        if vrt is None or vrt.RasterCount != 12:
            raise ValueError('WorldClim monthly grids cannot be assembled without resampling')
        vrt = None
        sources = [(sources[0][0], str(combined))]
    from pyproj import CRS
    source_registrations=[]
    for source_asset,path in sources:
        dataset=gdal.Open(path)
        if not dataset or not dataset.GetProjection():
            raise ValueError("Input raster lacks an interpretable CRS")
        expected=(source_asset.metadata.get('source_crs') if source_asset else None) or product.semantics.get("source_crs")
        if expected and not CRS.from_wkt(dataset.GetProjection()).to_2d().equals(CRS.from_user_input(expected)):
            raise ValueError("Input CRS contradicts the documented product reference system")
        if source_asset and source_asset.metadata.get('access_method','').startswith('USGS staged'):
            gt=dataset.GetGeoTransform()
            spacing=source_asset.metadata['native_spacing']
            if gt[2] or gt[4] or not math.isclose(abs(gt[1]),spacing['x'],rel_tol=1e-7) or not math.isclose(abs(gt[5]),spacing['y'],rel_tol=1e-7):
                raise ValueError('USGS raster native grid contradicts its retained FGDC metadata')
        expected_grid=source_asset.metadata.get('expected_native_grid') if source_asset else None
        if expected_grid:
            gt=dataset.GetGeoTransform()
            tolerance=expected_grid.get('spacing_absolute_tolerance',0)
            if gt[2] or gt[4] or gt[1]<=0 or gt[5]>=0 or dataset.RasterXSize != expected_grid['width'] or dataset.RasterYSize != expected_grid['height'] or not math.isclose(abs(gt[1]),expected_grid['x_spacing'],rel_tol=1e-10,abs_tol=tolerance) or not math.isclose(abs(gt[5]),expected_grid['y_spacing'],rel_tol=1e-10,abs_tol=tolerance):
                raise ValueError('Input grid contradicts the frozen native grid contract')
            if 'origin' in expected_grid and any(abs(actual-expected)>expected_grid.get('origin_tolerance',0) for actual,expected in zip((gt[0],gt[3]),expected_grid['origin'])):
                raise ValueError('Input grid origin contradicts the frozen tile registration')
            if 'bands' in expected_grid and dataset.RasterCount!=expected_grid['bands']:
                raise ValueError('Input band inventory contradicts the frozen product contract')
            for number in range(1,dataset.RasterCount+1):
                band=dataset.GetRasterBand(number)
                if 'nodata' in expected_grid and band.GetNoDataValue()!=expected_grid['nodata']:
                    raise ValueError('Input NoData contradicts the frozen product contract')
                if 'datatype' in expected_grid and gdal.GetDataTypeName(band.DataType)!=expected_grid['datatype']:
                    raise ValueError('Input datatype contradicts the frozen product contract')
                if 'scale' in expected_grid and (band.GetScale() if band.GetScale() is not None else 1)!=expected_grid['scale']:
                    raise ValueError('Input scale contradicts the frozen product contract')
                if 'offset' in expected_grid and (band.GetOffset() if band.GetOffset() is not None else 0)!=expected_grid['offset']:
                    raise ValueError('Input offset contradicts the frozen product contract')
        source_registrations.append({'asset_id':source_asset.id if source_asset else 'retained-local-vrt','crs':dataset.GetProjection(),'affine_grid':list(dataset.GetGeoTransform()),'dimensions':[dataset.RasterXSize,dataset.RasterYSize],'expected_grid':expected_grid})
        dataset=None
    results, findings, outputs = [], [], []
    cutline = workdir / "aoi.geojson"
    atomic_json(cutline, {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": plan.aoi}]})
    groups = [[entry] for entry in sources] if selection.recipe["native_export"] else [sources]
    for ordinal, group in enumerate(groups):
        if cancelled():
            raise Cancelled("Cancelled before processing")
        output = workdir / f"{selection.id}-{ordinal:04d}.tif"
        recipe = selection.recipe
        if recipe["native_export"]:
            from pyproj import CRS, Transformer
            from shapely.geometry import shape
            from shapely.ops import transform
            source = gdal.Open(group[0][1])
            if not source or not source.GetProjection():
                raise ValueError("Native source has no interpretable CRS")
            crs = CRS.from_wkt(source.GetProjection())
            if product.kind == "imagery":
                from .imagery import validate_registration
                validate_registration(source, group[0][0])
            from .projection import auxiliary_transformer
            bounds = transform(auxiliary_transformer(recipe,4326,crs,plan.aoi).transform, shape(plan.aoi)).bounds
            gt = source.GetGeoTransform()
            if gt[2] or gt[4]:
                raise ValueError("Rotated native grids require an explicit supported subset recipe")
            x0, x1 = sorted([(bounds[0]-gt[0])/gt[1], (bounds[2]-gt[0])/gt[1]])
            y0, y1 = sorted([(bounds[1]-gt[3])/gt[5], (bounds[3]-gt[3])/gt[5]])
            x, y = max(0, math.floor(x0)), max(0, math.floor(y0))
            w, h = min(source.RasterXSize, math.ceil(x1))-x, min(source.RasterYSize, math.ceil(y1))-y
            if w <= 0 or h <= 0:
                raise ValueError("Selected native raster does not intersect AOI")
            if w*h*source.RasterCount > 100_000_000:
                raise ValueError("Native subset exceeds the 100-million value limit")
            if shutil.disk_usage(workdir).free < w*h*source.RasterCount*24+16*1024**2:
                raise ValueError("Insufficient processing space for native subset and validation masks")
            subset = gdal.Translate(str(workdir / f"subset-{ordinal}.vrt"), source, format="VRT", srcWin=[x,y,w,h])
            if product.kind == "imagery":
                calibration = group[0][0].metadata.get("calibration")
                band = subset.GetRasterBand(1)
                band.SetNoDataValue(0)
                if calibration:
                    band.SetScale(calibration["scale"])
                    band.SetOffset(calibration["offset"])
                    band.SetUnitType(calibration["units"])
                else:
                    band.SetUnitType("class_code")
            ds = gdal.Translate(str(output), subset, format="COG", creationOptions=["COMPRESS=LZW", "NUM_THREADS=1"], callback=callback)
            subset = None
            role = "native_bbox_subset_with_aoi_mask"
        else:
            from .projection import frozen_operation,mask_after_resampling
            projected=[]
            for _, path in group:
                source = gdal.Open(path)
                if source is None or not source.GetProjection():
                    raise ValueError("Input raster lacks an interpretable CRS")
                validate_units(source, product)
                operation=frozen_operation(recipe,source.GetProjection())
                source = None
                intermediate=workdir/f'projected-{ordinal:04d}-{len(projected):04d}.tif'
                ds = gdal.Warp(str(intermediate), path, format='GTiff', dstSRS=recipe["target_crs"], coordinateOperation=operation['pipeline'],
                           outputBounds=recipe["extent"], xRes=recipe["spacing"][0], yRes=recipe["spacing"][1],
                           resampleAlg=recipe["resampling"], outputType=gdal.GDT_Float32 if product.category != "landcover" else gdal.GDT_Byte,
                           dstNodata=recipe["nodata"], multithread=False,
                           warpOptions=["NUM_THREADS=1", "CUTLINE_ALL_TOUCHED=TRUE", "APPLY_VERTICAL_SHIFT=NO"], transformerOptions=["ALLOW_BALLPARK=NO", "ONLY_BEST=YES"],
                           creationOptions=["COMPRESS=LZW", "NUM_THREADS=1", "BIGTIFF=IF_SAFER"], callback=callback)
                if ds is None:
                    raise ValueError('The frozen coordinate operation did not produce a complete raster')
                mask_after_resampling(ds,plan.aoi,cancelled,recipe)
                ds.FlushCache(); ds=None
                projected.append(intermediate)
            mosaic=gdal.BuildVRT('',[str(p) for p in projected])
            if mosaic is None:
                raise ValueError('Aligned source grids could not be merged')
            ds=gdal.Translate(str(output),mosaic,format='COG',creationOptions=['COMPRESS=LZW','NUM_THREADS=1'],callback=callback)
            mosaic=None
            for path in projected: path.unlink()
            role = "project_grid"
        if ds is None:
            raise ValueError("Raster processing did not finish")
        ds.FlushCache()
        ds = None
        gap = workdir / f"{selection.id}-{ordinal:04d}.gaps.tif"
        # Quality layers have a different class domain than the measured band.
        check_product = product.model_copy(deep=True)
        if group[0][0] and group[0][0].role == "quality":
            check_product.semantics = {**check_product.semantics, "classes": {str(i): str(i) for i in range(12)}}
            check_product.units = "class_code"
        elif product.kind == "imagery":
            check_product.units = "1"
        result, issues = validate_raster(output, plan.aoi, check_product, selection_id=f"{selection.id}:{ordinal}", cancelled=cancelled, gap_path=gap,recipe=recipe)
        source_metadata = group[0][0].metadata if len(group) == 1 and group[0][0] else {'assets':[{'asset_id':a.id,'metadata':a.metadata} for a,_ in group if a]}
        result["scientific_hash"] = digest({"raster": result["scientific_hash"], "source_metadata": source_metadata, "parameters": selection.parameters, 'recipe':scientific_recipe(recipe)})
        result.update({"selection_id": selection.id, "artifact": output.name, "gap_mask": gap.name, "source_metadata": source_metadata,'processing_recipe':recipe,'source_registrations':source_registrations})
        results.append(result)
        findings.extend(issues)
        outputs.append({"id": output.stem, "name": product.name + (f" {source_metadata.get('scene', '')} {source_metadata.get('band', '')}" if source_metadata.get("scene") else ""),
                        "category": product.category, "kind": "raster", "role": role, "file": output.name, "sha256": file_hash(output),
                        "scientific_hash": result["scientific_hash"], "gap_mask": gap.name, "gap_sha256": file_hash(gap), "product": product.model_dump(mode="json"), "parameters": selection.parameters,
                        "extent_gap": gap.with_suffix(".geojson").name, "extent_gap_sha256": file_hash(gap.with_suffix(".geojson")),
                        "source_metadata": source_metadata, "grid": result["grid"], "crs": result["crs"], "dimensions": result["dimensions"], "bbox_wgs84":result["bbox_wgs84"], "recipe": recipe})
        if product.adapter == 'worldclim':
            from .climatology import export_climatology
            nc_path = output.with_suffix('.nc')
            cf_result = export_climatology(output,gap,nc_path,product,result['scientific_hash'],cancelled)
            cf_result['selection_id'] = selection.id
            results.append(cf_result)
            # A twelve-month analytical cube must never be interpreted as RGB.
            outputs[-1]['kind'] = 'climate'
            outputs.append({'id':output.stem+'-cf','name':product.name+' monthly CF export','category':'climate','kind':'climate','role':'native_cf_climatology',
                            'file':nc_path.name,'sha256':file_hash(nc_path),'scientific_hash':cf_result['scientific_hash'],'product':product.model_dump(mode='json'),
                            'parameters':selection.parameters,'recipe':{'operation':'lossless native grid to CF-1.12 monthly climatology','source_scientific_hash':result['scientific_hash']}})
            preview = output.with_name(output.stem+'-january-preview.tif')
            preview_ds = gdal.Translate(str(preview),str(output),format='COG',bandList=[1],creationOptions=['COMPRESS=LZW','NUM_THREADS=1'],callback=callback)
            if preview_ds is None:
                raise ValueError('Climatology preview did not finish')
            preview_ds = None
            preview_product = product.model_copy(deep=True)
            preview_product.semantics.pop('expected_bands',None)
            preview_result, preview_issues = validate_raster(preview,plan.aoi,preview_product,selection_id=selection.id+':preview',cancelled=cancelled,recipe=recipe)
            preview_result.update({'selection_id':selection.id,'artifact':preview.name,'role':'visualization_derivative','month':1})
            results.append(preview_result)
            findings.extend(preview_issues)
            outputs.append({'id':preview.stem,'name':product.name+' — January preview (1970–2000 climatology)','category':'climate','kind':'raster','role':'visualization_derivative',
                            'file':preview.name,'sha256':file_hash(preview),'scientific_hash':preview_result['scientific_hash'],'product':preview_product.model_dump(mode='json'),
                            'gap_mask':gap.name,'gap_sha256':file_hash(gap),
                            'parameters':selection.parameters,'crs':preview_result['crs'],'grid':preview_result['grid'],'dimensions':preview_result['dimensions'],'bbox_wgs84':preview_result['bbox_wgs84'],
                            'recipe':{'operation':'band 1 display derivative; no value resampling','source_scientific_hash':result['scientific_hash']}})
    return outputs, results, findings


