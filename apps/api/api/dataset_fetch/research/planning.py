from __future__ import annotations

import importlib.metadata
import json
import math
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from .contracts import Asset, FetchPlan, Finding, PlanRequest, PlannedSelection, canonical, digest, file_hash, now
from .registry import parameters, registry
from .store import ROOT, Store
from .transport import Transport

MAX_CELLS = 100_000_000
MAX_ASSETS = 2048
LOADED_IMPLEMENTATION_FILES = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}


def runtime_fingerprint():
    from osgeo import gdal, osr
    import pyproj
    pyproj.network.set_network_enabled(False)
    directories = {"pyproj": Path(pyproj.datadir.get_data_dir())}
    # Ignore nonexistent search paths before numbering; GDAL normalizes that list
    # on first use without changing the actual transformation resources.
    directories.update({f"gdal_{i}": p for i, p in enumerate(dict.fromkeys(Path(p).resolve() for p in osr.GetPROJSearchPaths() if Path(p).is_dir()))})
    grids = {label: {p.relative_to(directory).as_posix(): file_hash(p) for p in sorted(directory.rglob("*"))
                    if p.is_file() and p.suffix in (".db", ".tif", ".gsb", ".gtx")} for label, directory in directories.items()}
    builds = []
    for path in sorted((Path(sys.prefix)/'conda-meta').glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        builds.append({k:record.get(k) for k in ('name','version','build','sha256','md5')})
    return {"python": platform.python_version(), "platform": sys.platform, "architecture": platform.machine(), "gdal": gdal.VersionInfo("RELEASE_NAME"),
            "native_package_builds": builds,
            "proj": pyproj.proj_version_str, "gdal_proj": [osr.GetPROJVersionMajor(), osr.GetPROJVersionMinor(), osr.GetPROJVersionMicro()],
            "packages": {name: importlib.metadata.version(name) for name in ("numpy", "shapely", "pyproj", "requests", "pydantic", "xarray", "netCDF4", "cftime", "pandas", "cdsapi", "access-parser", "construct", "tabulate")},
            "proj_data": grids, "threads": 1, "proj_network": False, "implementation_files": implementation_files(), "protocol_implementation": implementation_hash()}


def implementation_files():
    return dict(LOADED_IMPLEMENTATION_FILES)


def implementation_hash():
    return digest(implementation_files())


def context(project):
    from ..utils import _load_project_context
    from .aoi import read_aoi
    ctx = _load_project_context(project)
    aoi, ctx.aoi_provenance = read_aoi(ctx.cutline_path)
    return ctx, aoi


def split_bounds(aoi):
    from shapely.geometry import shape
    geom = shape(aoi)
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for part in parts:
        west, south, east, north = part.bounds
        if east - west > 180:
            raise ValueError("AOI crosses the antimeridian without a split geometry; split it at ±180° before planning")
        yield west, south, east, north


def grid_recipe(aoi, target_crs, product):
    from pyproj import CRS, Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    crs = CRS.from_user_input(target_crs)
    if product.kind in ("imagery","climate","population") or product.semantics.get("native_export"):
        from pyproj import Geod
        nominal=product.semantics.get("resolution_m", {"imagery":10,"climate":31000,"population":100}.get(product.kind,1000))
        area=abs(Geod(ellps="WGS84").geometry_area_perimeter(shape(aoi))[0])
        return {"native_export":True,"target_crs":target_crs,"extent":None,"spacing":None,"spacing_units":None,
                "cells":max(1,math.ceil(area/nominal**2)),"estimate_basis":"AOI geodesic area / nominal native cell area; actual native bounds checked after acquisition",
                "resampling":"none","threads":1,"mask":"Native bounding-box subset plus AOI validity and extent-gap masks","nodata":"preserve source","transform_network":False,"allow_ballpark":False}
    from .projection import resolve_operation
    grid_operation=resolve_operation(4326,crs,aoi,always_xy=True)
    bounds = transform(Transformer.from_pipeline(grid_operation['pipeline']).transform, shape(aoi)).bounds
    nominal = product.semantics.get("resolution_m", 30)
    spacing = nominal / (crs.axis_info[0].unit_conversion_factor if crs.is_projected else 111320)
    extent = [math.floor(bounds[0]/spacing)*spacing, math.floor(bounds[1]/spacing)*spacing,
              math.ceil(bounds[2]/spacing)*spacing, math.ceil(bounds[3]/spacing)*spacing]
    cells = round((extent[2]-extent[0])/spacing) * round((extent[3]-extent[1])/spacing)
    if cells > MAX_CELLS:
        raise ValueError(f"Output would contain {cells:,} cells; the limit is {MAX_CELLS:,}. Reduce the AOI.")
    return {"target_crs": target_crs, "extent": extent, "spacing": [spacing, spacing], "spacing_units": crs.axis_info[0].unit_name,
            "cells": cells, "resampling": "near" if product.category in ("landcover", "geohazard") else "bilinear", "threads": 1,
            "mask": "positive AOI-cell intersection; fractional coverage reported", "nodata": product.semantics.get("nodata", -9999),
            "native_export": product.kind in ("imagery", "climate", "population") or product.semantics.get("native_export", False), "transform_network": False, "allow_ballpark": False,'grid_extent_operation':grid_operation}


def build_plan(project: str, request: PlanRequest, store: Store, *, cancelled=lambda:False, progress=lambda *args:None):
    from .discovery import discover
    from shapely.geometry import shape
    import time
    from .transport import Cancelled
    started=time.monotonic()
    def check():
        if time.monotonic()-started>1800:
            raise ValueError('Discovery exceeded its 30-minute deadline; reduce the AOI or selections')
        if cancelled():raise Cancelled('Discovery cancelled before confirmation')
        return False
    check()
    ctx, aoi = context(project)
    list(split_bounds(aoi))
    as_of = request.as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("as_of requires a timezone")
    if as_of > datetime.now(timezone.utc):
        raise ValueError("as_of cannot be in the future")
    as_of = as_of.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    products = registry()
    planned = []
    total_bytes = 0
    unknown_size = False
    transport = Transport(store,cancelled=check,max_total_bytes=2*1024**3)
    from .aoi import preserve_aoi
    aoi_receipts, aoi_findings = preserve_aoi(ctx, transport)
    from .country_selection import country_selections
    for selection in country_selections(request.selections, products, ctx.iso3_list):
        check()
        progress('discovering', selection.product_id)
        if selection.product_id not in products:
            raise ValueError(f"Unknown product ID: {selection.product_id}")
        product = products[selection.product_id].model_copy(deep=True)
        from .qualification import attach_verification
        attach_verification(product)
        if product.assessment.disposition in ("excluded", "unavailable"):
            raise ValueError(f"{product.name} is {product.assessment.disposition}")
        missing = [name for name in product.credentials if not os.environ.get(name)]
        if missing:
            raise ValueError("Missing provider credentials: " + ", ".join(missing))
        params = parameters(product, selection.parameters)
        recipe = grid_recipe(aoi, f"EPSG:{ctx.target_epsg}", product)
        if getattr(ctx, 'aoi_provenance', None):
            recipe['aoi_normalization'] = ctx.aoi_provenance
        if product.kind=='vector':
            recipe['native_measures']='Preserve optional M values as original ISO WKB and CRS in attributes; clipped XY/Z project geometry is a derivative. Unknown M units remain unqualified and require a specific post-fetch acknowledgement. No M interpolation.'
        assets, snapshots, findings = discover(product, params, aoi, as_of, transport)
        if product.adapter=='soilgrids':recipe['source_preparation']=product.semantics['vrt_interpretation']
        if product.adapter=='os_terrain50':recipe['source_preparation']=product.semantics['source_preparation']
        if product.adapter=='hwsd':recipe['native_attributes']='Original MDB, complete native row verification, all mapping-unit components/depths; no property interpolation or averaging'
        snapshots.extend(aoi_receipts)
        findings.extend(aoi_findings)
        countries = getattr(ctx, 'iso3_list', [])
        if 'WLD' not in product.countries and (not countries or not set(countries) & set(product.countries)):
            findings.append(Finding(id='declared-country-coverage',rule='provider-coverage',severity='acknowledgement',
                message='The selected product declares coverage of '+', '.join(product.countries)+'. Project country hints are '+(', '.join(countries) or 'unknown')+'. A verified empty provider response does not establish absence of real-world features outside the provider coverage.',
                evidence={'product_countries':product.countries,'project_country_hints':countries}))
        if product.adapter=='tnm' and params.get('catalogue') in ('staged_tiles','project_index') and assets:
            from .usgs import describe_selection
            describe_selection(product,assets)
        from .projection import freeze_raster_operations,freeze_auxiliary_operations
        findings.extend(freeze_raster_operations(product,assets,recipe,aoi))
        findings.extend(freeze_auxiliary_operations(product,assets,recipe,aoi))
        from .evidence import documentary_evidence
        progress('retaining_documentation', product.id)
        documentation, missing_documents = documentary_evidence(product, transport)
        snapshots.extend(documentation)
        findings.extend(missing_documents)
        dates=sorted({a.metadata.get("properties",{}).get("datetime") for a in assets if a.metadata.get("properties",{}).get("datetime")})
        if dates:
            product.observation_period={"start":dates[0],"end":dates[-1],"basis":"retained item observation datetimes; individual dates preserved per asset"}
        if product.kind=="imagery":
            product.native_spacing={a.metadata["band"]: {"gsd_m":a.metadata["asset"].get("gsd"),"grid":a.metadata["asset"].get("proj:transform")} for a in assets}
        if product.kind=="population" and "year" in params:
            product.observation_period={"reference_year":params["year"],"basis":"population model reference year, not a simultaneous population observation"}
        assets = sorted(assets, key=lambda a: a.id)
        if len(assets) > MAX_ASSETS or len({a.id for a in assets}) != len(assets):
            raise ValueError("Too many or duplicate assets")
        if not assets and product.adapter not in ("arcgis", "overpass", "cds", "ogc_features"):
            raise ValueError("No assets intersect the requested AOI")
        sid = digest({"product": product.id, "parameters": params})[:20]
        if any(s.id == sid for s in planned):
            raise ValueError("Duplicate dataset selection")
        for asset in assets:
            total_bytes += asset.size or 0
            unknown_size |= asset.size is None
        all_findings = product.assessment.findings + findings
        # Scope acknowledgements to the selection, not merely the provider.
        all_findings = [f.model_copy(update={"id": f"{sid}:{f.id}"}) for f in all_findings]
        planned.append(PlannedSelection(id=sid, product=product, parameters=params, assets=assets, discovery=snapshots, recipe=recipe, findings=all_findings))
    estimate = {"known_download_bytes": total_bytes, "unknown_asset_sizes": unknown_size,
                "output_upper_bound_bytes": sum(s.recipe["cells"]*16*max(1,len(s.assets) if s.recipe["native_export"] else 1)*s.product.semantics.get("expected_bands",1) for s in planned),
                "output_size_is_estimate":True,"input_retention": "until explicitly deleted",
                "intermediate_upper_bound_bytes":sum(s.recipe['cells']*8*len(s.assets) for s in planned if not s.recipe['native_export'] and s.product.kind=='raster')+sum(256*1024**2 for s in planned if s.product.adapter=='hwsd'),
                "maximum_asset_bytes": 20*1024**3, "maximum_job_bytes": 100*1024**3}
    if total_bytes > estimate["maximum_job_bytes"] or any((a.size or 0) > estimate["maximum_asset_bytes"] for s in planned for a in s.assets):
        raise ValueError("The frozen asset inventory exceeds the acquisition storage limit; select a smaller product or AOI")
    if estimate['intermediate_upper_bound_bytes']>estimate['maximum_job_bytes']:
        raise ValueError('The frozen processing recipe exceeds the intermediate storage limit')
    body = {"schema_version": "zeus.acquisition/2.0", "project": project, "aoi": aoi, "aoi_hash": digest(aoi), "target_crs": f"EPSG:{ctx.target_epsg}",
            "as_of": as_of, "runtime": runtime_fingerprint(), "policy": "complete-extract/1.0", "selections": [s.model_dump(mode="json") for s in planned], "findings": [], "estimates": estimate}
    h = digest(body)
    plan = FetchPlan(**body, plan_id=h[:32], plan_hash=h)
    check()
    progress('freezing_plan', None)
    # Preserve the implementation used by this plan, even after the checkout changes.
    for filename, sha in plan.runtime["implementation_files"].items():
        receipt = transport.retain_bytes((Path(__file__).parent / filename).read_bytes(), "zeus:implementation/"+filename)
        if receipt.sha256 != sha:
            raise ValueError("Implementation changed while freezing the plan")
    store.save_plan(plan)
    return plan
