from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .contracts import Finding, canonical, digest


def validate_units(dataset, product):
    aliases = {"metre": "m", "meter": "m", "metres": "m", "meters": "m", "degrees celsius": "degc", "celsius": "degc", "dimensionless": "1"}
    expected = aliases.get(product.units.lower(), product.units.lower())
    if expected in ("", "unknown", "none"):
        raise ValueError("Mandatory scientific units cannot be interpreted")
    for index in range(1, dataset.RasterCount+1):
        band = dataset.GetRasterBand(index)
        actual = band.GetUnitType().strip().lower()
        if actual and aliases.get(actual, actual) != expected:
            raise ValueError(f"Raster band {index} units {actual!r} contradict the documented product units {product.units!r}")
        for value in (band.GetScale(), band.GetOffset()):
            if value is not None and not math.isfinite(value):
                raise ValueError("Raster calibration is nonfinite")


def validate_raster(path: Path, aoi, product, *, selection_id, cancelled=lambda: False, gap_path=None, legacy_inventory=False,recipe=None):
    """Full scan, all bands. Area fractions are explicitly measured in the raster CRS.

    Only boundary cells need geometric intersection. Interior cells use the exact
    affine cell area; no sampling or file-size shortcuts are used.
    """
    from osgeo import gdal, ogr, osr
    from pyproj import CRS, Transformer
    from shapely.geometry import shape, Polygon
    from shapely.ops import transform
    from .transport import Cancelled
    gdal.UseExceptions()
    ds = gdal.Open(str(path))
    if ds is None or not ds.GetProjection():
        raise ValueError("Raster is unreadable or lacks a CRS")
    if not legacy_inventory:
        validate_units(ds, product)
    if ds.RasterXSize*ds.RasterYSize*ds.RasterCount > 100_000_000:
        raise ValueError("Raster exceeds the complete-scan limit of 100 million values")
    if product.semantics.get("expected_bands") and ds.RasterCount != product.semantics["expected_bands"]:
        raise ValueError("Raster is missing required bands or time slices")
    crs = CRS.from_wkt(ds.GetProjection())
    from .projection import auxiliary_transformer
    forward=auxiliary_transformer(recipe,4326,crs,aoi)
    reverse=auxiliary_transformer(recipe,crs,4326,aoi)
    geom = transform(forward.transform, shape(aoi))
    if geom.is_empty or not geom.is_valid or not geom.area > 0:
        raise ValueError("AOI transformation is invalid")
    gt = ds.GetGeoTransform()
    if not all(math.isfinite(v) for v in gt) or gt[1]*gt[5]-gt[2]*gt[4] == 0:
        raise ValueError("Raster grid is singular or invalid")
    if gap_path:
        from shapely.geometry import mapping
        from .store import atomic_json
        corners = [(gt[0]+x*gt[1]+y*gt[2],gt[3]+x*gt[4]+y*gt[5]) for x,y in [(0,0),(ds.RasterXSize,0),(ds.RasterXSize,ds.RasterYSize),(0,ds.RasterYSize)]]
        missing_extent = geom.difference(Polygon(corners))
        gap_geometry = transform(reverse.transform, missing_extent)
        features = [] if missing_extent.is_empty else [{"type":"Feature","properties":{"gap":"outside retained raster extent","applies_to":"every band"},"geometry":mapping(gap_geometry)}]
        atomic_json(gap_path.with_suffix(".geojson"), {"type":"FeatureCollection","features":features})
    mem = ogr.GetDriverByName("Memory").CreateDataSource("")
    ref = osr.SpatialReference(wkt=ds.GetProjection())
    layer = mem.CreateLayer("aoi", ref, ogr.wkbUnknown)
    f = ogr.Feature(layer.GetLayerDefn())
    f.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(aoi)))
    # Rasterization uses a geometry already transformed with strict PROJ settings.
    f.SetGeometry(ogr.CreateGeometryFromWkb(geom.wkb))
    layer.CreateFeature(f)
    mask = gdal.GetDriverByName("MEM").Create("", ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Byte)
    mask.SetGeoTransform(gt)
    mask.SetProjection(ds.GetProjection())
    gdal.RasterizeLayer(mask, [1], layer, burn_values=[1], options=["ALL_TOUCHED=TRUE"])
    gaps = None
    if gap_path:
        gaps = gdal.GetDriverByName("GTiff").Create(str(gap_path), ds.RasterXSize, ds.RasterYSize, ds.RasterCount, gdal.GDT_Byte,
                                                    options=["TILED=YES", "COMPRESS=LZW"])
        gaps.SetGeoTransform(gt)
        gaps.SetProjection(ds.GetProjection())
        for i in range(ds.RasterCount):
            gaps.GetRasterBand(i+1).SetNoDataValue(255)
            gaps.GetRasterBand(i+1).SetDescription("0=valid; 1=missing/invalid; 255=outside AOI")
    band_results, findings = [], []
    science = hashlib.sha256(canonical({"aoi_hash": digest(aoi), "crs": crs.to_wkt(), "grid": gt, "shape": [ds.RasterYSize, ds.RasterXSize], "bands": ds.RasterCount, "units": product.units, "semantics": product.semantics}))
    cell_area = abs(gt[1]*gt[5]-gt[2]*gt[4])
    for index in range(1, ds.RasterCount+1):
        band = ds.GetRasterBand(index)
        nodata = band.GetNoDataValue()
        valid_area, touched_area, valid_count, total_count, invalid_domain = 0., 0., 0, 0, 0
        nonfinite_unmasked=0
        min_value, max_value = None, None
        class_coverage={code:{'label':label,'cells':0,'area':0.} for code,label in product.semantics.get('classes',{}).items()}
        science.update(canonical({"band": index, "type": gdal.GetDataTypeName(band.DataType), "scale": band.GetScale(), "offset": band.GetOffset(), "unit": band.GetUnitType()}))
        for y in range(0, ds.RasterYSize, 256):
            for x in range(0, ds.RasterXSize, 256):
                if cancelled():
                    raise Cancelled("Cancelled during validation")
                w, h = min(256, ds.RasterXSize-x), min(256, ds.RasterYSize-y)
                data = band.ReadAsArray(x, y, w, h)
                if data is None:
                    raise ValueError("Raster block could not be read")
                valid = band.GetMaskBand().ReadAsArray(x, y, w, h) != 0
                unexpected_nonfinite=valid & ~np.isfinite(data)
                valid &= np.isfinite(data)
                if nodata is not None and math.isfinite(nodata):
                    valid &= data != nodata
                aoi_mask = mask.ReadAsArray(x, y, w, h).astype(bool)
                domain = np.ones(data.shape, dtype=bool)
                classes = product.semantics.get("classes")
                if classes:
                    domain &= np.isin(data, [int(v) for v in classes])
                if product.kind == "population" or product.category in ("soil", "geohazard"):
                    domain &= data >= 0
                limits = product.semantics.get('value_range')
                if limits:
                    if limits[0] is not None: domain &= data >= limits[0]
                    if limits[1] is not None: domain &= data <= limits[1]
                outside_domain=valid & ~domain
                valid &= domain
                yy, xx = np.mgrid[y:y+h, x:x+w]
                # Every touched cell is checked geometrically; contains_xy is insufficient
                # for narrow holes. Vectorized Shapely predicates bound memory per block.
                import shapely
                corners = np.stack([np.stack([gt[0]+(xx+dx)*gt[1]+(yy+dy)*gt[2], gt[3]+(xx+dx)*gt[4]+(yy+dy)*gt[5]], axis=-1) for dx, dy in [(0,0),(1,0),(1,1),(0,1),(0,0)]], axis=-2)
                cells = shapely.polygons(corners)
                weights = np.zeros((h,w), dtype=np.float64)
                inside = shapely.contains(geom, cells) & aoi_mask
                weights[inside] = cell_area
                boundary = aoi_mask & ~inside
                if np.any(boundary):
                    weights[boundary] = shapely.area(shapely.intersection(cells[boundary], geom))
                included = weights > 0
                invalid_domain += int(np.count_nonzero(outside_domain & included))
                nonfinite_unmasked+=int(np.count_nonzero(unexpected_nonfinite & included))
                total_count += int(np.count_nonzero(included))
                valid_count += int(np.count_nonzero(valid & included))
                touched_area += float(np.sum(weights, dtype=np.float64))
                valid_area += float(np.sum(weights[valid], dtype=np.float64))
                if np.any(valid & included):
                    values = data[valid & included]
                    lo, hi = float(values.min()), float(values.max())
                    min_value = lo if min_value is None else min(min_value, lo)
                    max_value = hi if max_value is None else max(max_value, hi)
                for code,metrics in class_coverage.items():
                    selected=valid & included & (data==int(code))
                    metrics['cells']+=int(np.count_nonzero(selected))
                    metrics['area']+=float(np.sum(weights[selected],dtype=np.float64))
                normalized = np.where(valid & included, data, 0).astype(data.dtype.newbyteorder("<"))
                normalized[normalized == 0] = 0
                science.update(np.ascontiguousarray(normalized).tobytes())
                science.update(np.ascontiguousarray(valid & included, dtype=np.uint8).tobytes())
                if gaps:
                    gaps.GetRasterBand(index).WriteArray(np.where(included, np.where(valid, 0, 1), 255).astype(np.uint8), x, y)
        missing_area = max(0., geom.area-valid_area)
        fraction = min(1., valid_area/geom.area)
        result = {"band": index, "valid_cells": valid_count, "intersecting_cells": total_count, "valid_fraction": fraction,
                  "valid_area": valid_area, "aoi_area": geom.area, "outside_raster_area": max(0., geom.area-touched_area),
                  "area_units": crs.axis_info[0].unit_name+" squared", "area_method": "planar AOI-cell intersection in raster CRS",
                  "minimum": min_value, "maximum": max_value, "invalid_domain_cells": invalid_domain, "nonfinite_unmasked_cells":nonfinite_unmasked, "nodata": nodata if nodata is None or math.isfinite(nodata) else "NaN"}
        if class_coverage:
            result['class_coverage']={code:{**metrics,'aoi_fraction':metrics['area']/geom.area} for code,metrics in class_coverage.items()}
            for code,meaning in product.semantics.get('non_surface_classes',{}).items():
                metrics=result['class_coverage'].get(code,{})
                if metrics.get('cells',0):
                    findings.append(Finding(id=f'{selection_id}:band{index}:class{code}',rule='landcover-non-surface-class',severity='acknowledgement',
                        message=f"Band {index}: {metrics['aoi_fraction']*100:.6f}% of the AOI is {meaning}. These are valid provider codes; suitability for surface analysis needs review.",
                        basis=product.assessment.documentation[0],evidence={'class_code':code,**metrics,'area_units':result['area_units']}))
        band_results.append(result)
        if nonfinite_unmasked:
            findings.append(Finding(id=f'{selection_id}:band{index}:nonfinite',rule='finite-scientific-values',severity='block',
                message=f'Band {index} contains {nonfinite_unmasked} unmasked nonfinite AOI values not identified as source NoData',evidence=result))
        if invalid_domain:
            findings.append(Finding(id=f"{selection_id}:band{index}:domain", rule="documented-value-domain", severity="block", message=f"Band {index} contains {invalid_domain} out-of-domain cells", evidence=result))
        if valid_count == 0:
            findings.append(Finding(id=f"{selection_id}:band{index}:empty", rule="nonempty-raster", severity="block", message=f"Band {index} has no valid AOI data", evidence=result))
        elif missing_area > max(geom.area*1e-10, cell_area*1e-8):
            findings.append(Finding(id=f"{selection_id}:band{index}:gap", rule="aoi-valid-coverage", severity="acknowledgement", message=f"Band {index}: {100*fraction:.6f}% valid AOI coverage; review missing areas", evidence=result))
    if gaps:
        gaps.FlushCache()
        gaps = None
    corners = [(gt[0]+x*gt[1]+y*gt[2],gt[3]+x*gt[4]+y*gt[5]) for x,y in [(0,0),(ds.RasterXSize,0),(ds.RasterXSize,ds.RasterYSize),(0,ds.RasterYSize)]]
    bbox = transform(reverse.transform, Polygon(corners)).bounds
    rule_results=[{"rule":rule,"status":"passed","basis":"ZEUS policy: complete-extract/1.0"} for rule in ("interpretable-crs","interpretable-units","finite-calibration","nonsingular-grid","complete-band-scan")]
    if legacy_inventory:
        for rule in rule_results:
            if rule['rule'] in ('interpretable-units','finite-calibration'):
                rule.update({'status':'not_assessed','reason':'Legacy inventory records existing encoded values without asserting a historical scientific unit or calibration contract'})
    rule_results.extend({"rule":f.rule,"status":"failed" if f.severity=="block" else "requires_acknowledgement","finding_id":f.id,"basis":f.basis} for f in findings)
    return {"kind": "raster", "bands": band_results, "scientific_hash": science.hexdigest(), "crs": crs.to_string(), "grid": list(gt), "dimensions": [ds.RasterXSize, ds.RasterYSize], "bbox_wgs84":list(bbox),"rule_results":rule_results,"fitness_for_analysis":"not assessed"}, findings



from .vector_validation import validate_vector
