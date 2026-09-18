from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from contextlib import contextmanager

import numpy as np
from .netcdf_compat import load_netcdf
load_netcdf()

from .contracts import Finding, canonical, digest, file_hash, scientific_recipe
from .transport import Cancelled

VARIABLES = {"2m_temperature": ("t2m", "K"), "total_precipitation": ("tp", "m"), "10m_u_component_of_wind": ("u10", "m s**-1"),
             "10m_v_component_of_wind": ("v10", "m s**-1"), "surface_pressure": ("sp", "Pa")}


def validate_climate(path, variables, expected_day, cancelled=lambda: False, *, allow_spatial_gaps=False):
    import xarray as xr
    with xr.open_dataset(path, engine="netcdf4", decode_cf=False) as raw:
        time_name = "valid_time" if "valid_time" in raw.variables else "time"
        if time_name not in raw or not raw[time_name].attrs.get("units"):
            raise ValueError("Climate time coordinate or units are missing")
        calendar = raw[time_name].attrs.get("calendar", "standard")
        if calendar not in ("standard", "gregorian", "proleptic_gregorian"):
            raise ValueError("ERA5 returned a calendar inconsistent with its Gregorian time contract")
        leap_seconds=raw[time_name].attrs.get('units_metadata','leap_seconds: unknown')
        if leap_seconds not in ('leap_seconds: none','leap_seconds: unknown'):
            raise ValueError('ERA5 leap-second semantics are invalid or unsupported by the pinned time decoder')
        decoded = xr.decode_cf(raw)
        actual = np.asarray(decoded[time_name].values).astype("datetime64[ns]")
        expected = np.arange(np.datetime64(expected_day, "h"), np.datetime64(expected_day, "h")+np.timedelta64(24, "h"), np.timedelta64(1, "h"))
        if actual.shape != expected.shape or not np.array_equal(actual, expected):
            raise ValueError("Climate response has missing, duplicate or unexpected time steps")
        for coordinate, unit, bounds in (("latitude","degrees_north",(-90,90)),("longitude","degrees_east",(-180,360))):
            if coordinate not in decoded or decoded[coordinate].attrs.get("units") != unit:
                raise ValueError("Climate horizontal coordinate units are missing or invalid")
            values = decoded[coordinate].values
            if values.ndim != 1 or not np.all(np.isfinite(values)) or np.any(values < bounds[0]) or np.any(values > bounds[1]) or len(np.unique(values)) != values.size:
                raise ValueError("Climate coordinate registration is invalid")
            if values.size > 1 and not (np.all(np.diff(values)>0) or np.all(np.diff(values)<0)):
                raise ValueError("Climate grid coordinates must be strictly ordered")
        h = hashlib.sha256()
        results = []
        for requested in variables:
            name, expected_units = VARIABLES[requested]
            if name not in decoded:
                raise ValueError(f"Climate variable missing: {requested}")
            data = decoded[name]
            units = data.attrs.get("units")
            if units != expected_units:
                raise ValueError(f"Unexpected climate units for {requested}: {units!r}")
            if requested=='2m_temperature' and data.attrs.get('units_metadata','temperature: on_scale')!='temperature: on_scale':
                raise ValueError('ERA5 temperature must represent an absolute temperature on its stated scale')
            if "latitude" not in data.dims or "longitude" not in data.dims or time_name not in data.dims:
                raise ValueError("Climate horizontal coordinate system is missing")
            expected_step = "accum" if requested == "total_precipitation" else "instant"
            if data.attrs.get("GRIB_stepType") != expected_step:
                raise ValueError(f"Climate accumulation semantics are missing or contradictory for {requested}")
            allowed_dims = {time_name,"latitude","longitude"}
            if set(data.dims) != allowed_dims:
                raise ValueError("Unexpected climate levels or ensemble dimensions")
            if data.size > 100_000_000:
                raise ValueError("Climate variable exceeds the 100-million value limit")
            attrs = {k: str(v) for k, v in sorted(data.attrs.items())}
            h.update(canonical({"variable": requested, "dims": data.dims, "attributes": attrs, "calendar": calendar, "time_units": raw[time_name].attrs["units"], 'time_units_metadata':leap_seconds,
                                'packing':{k:str(raw[name].attrs[k]) for k in ('scale_factor','add_offset','_FillValue','missing_value') if k in raw[name].attrs}}))
            for coord in data.coords:
                values = np.asarray(data[coord].values)
                h.update(canonical({"coordinate": coord, "shape": values.shape, "dtype": str(values.dtype)}))
                h.update(values.astype(str).tobytes() if values.dtype.kind in "OUS" else values.astype(values.dtype.newbyteorder("<")).tobytes())
            missing, count = 0, 0
            for t in range(len(expected)):
                for y in range(0, data.sizes["latitude"], 128):
                    if cancelled():
                        raise Cancelled("Cancelled during climate validation")
                    block = np.asarray(data.isel({time_name: t, "latitude": slice(y,y+128)}).values)
                    valid = np.isfinite(block)
                    missing += int(np.count_nonzero(~valid))
                    count += block.size
                    h.update(np.where(valid, block, 0).astype(block.dtype.newbyteorder("<")).tobytes())
                    h.update(valid.astype(np.uint8).tobytes())
            if missing and not allow_spatial_gaps:
                raise ValueError(f"Climate variable {requested} has {missing} missing values; time-dependent gap approval is not yet supported")
            results.append({"variable": requested, "stored_name": name, "units": units, "time_steps": 24, "values_scanned": count, "missing_values":missing,
                            "calendar": calendar, "cell_methods": data.attrs.get("cell_methods"), "step_type": data.attrs.get("GRIB_stepType")})
        return {"scientific_hash": h.hexdigest(), "variables": results, "date": expected_day, "kind": "climate", 'time_units_metadata':leap_seconds, "expver": raw["expver"].values.tolist() if "expver" in raw else None}


def process_climate(selection, receipts, plan, store, workdir, cancelled):
    from .climate_acquisition import cds_requests
    outputs, results, findings = [], [], []
    descriptor=next(d['cds'] for d in selection.discovery if 'cds' in d)
    inventory=cds_requests(selection.product,selection.parameters,descriptor['bbox'])
    if inventory!=descriptor['requests']:
        raise ValueError('Climate request inventory contradicts the frozen selection')
    data_ids=[r.asset_id for r in receipts if r.asset_id.startswith('climate:')]
    if sorted(data_ids)!=sorted(item['id'] for item in inventory):
        raise ValueError('Climate receipts do not contain every requested day and variable exactly once')
    by_id={r.asset_id:r for r in receipts}
    for item in inventory:
        receipt=by_id[item['id']]
        request_receipt=by_id.get('cds-request:'+item['id'])
        metadata_receipt=by_id.get('cds-metadata:'+item['id'])
        if not request_receipt or not metadata_receipt:
            raise ValueError('Climate receipt is missing its exact request or provider result metadata')
        if json.loads(store.verify_blob(request_receipt.sha256).read_text())!=item:
            raise ValueError('The retained CDS request differs from the approved request')
        metadata=json.loads(store.verify_blob(metadata_receipt.sha256).read_text())
        if metadata['content_length']!=receipt.size:
            raise ValueError('Climate input size contradicts the provider result metadata')
        source = store.verify_blob(receipt.sha256)
        one=selection.model_copy(deep=True)
        one.parameters={**selection.parameters,'variables':[item['variable']]}
        filename=f"{selection.id}-{item['date']}-{item['variable']}.nc"
        with climate_member(source,workdir,cancelled) as path:
            result=validate_climate(path,[item['variable']],item['date'],cancelled,allow_spatial_gaps=True)
            from .climate_spatial import export_climate
            spatial,gaps,evidence=export_climate(path,workdir/filename,one,plan,item['date'],cancelled)
        result['scientific_hash'] = digest({'native_climate':result['scientific_hash'],'spatial':[r['scientific_hash'] for r in spatial],'temporal_policy':'ERA5-HRES-instant-or-one-hour-ending-at-valid-time/1','recipe':scientific_recipe(selection.recipe)})
        findings.extend(gaps); outputs.extend(evidence); results.extend(spatial)
        result.update({"selection_id": selection.id, "artifact": filename,'request_hash':digest(item),'variable':item['variable']})
        results.append(result)
        if result['time_units_metadata']=='leap_seconds: unknown':
            findings.append(Finding(id=f'{selection.id}:{receipt.asset_id}:leap-seconds',rule='climate-time-interpretation',severity='acknowledgement',
                message='The provider response does not specify how leap seconds enter its Gregorian timeline. The CF export retains that uncertainty; hourly coordinates are decoded using the pinned calendar library.',
                basis='https://cfconventions.org/Data/cf-conventions/cf-conventions-1.12/cf-conventions.html#leap-seconds'))
        if result["expver"] is None:
            findings.append(Finding(id=f"{selection.id}:{receipt.asset_id}:preliminary", rule="climate-finality", severity="acknowledgement", message="Returned climate response does not identify expver; preliminary versus final status is unknown.", basis="https://confluence.ecmwf.int/pages/viewpage.action?pageId=177484060"))
        else:
            versions=set(np.asarray(result["expver"]).reshape(-1).tolist())
            if not versions <= {1,5}:
                raise ValueError("Climate expver contains an unsupported release status")
            if 5 in versions:
                findings.append(Finding(id=f"{selection.id}:{receipt.asset_id}:era5t",rule="climate-finality",severity="acknowledgement",message="This response includes preliminary ERA5T values (expver 5), which the publisher may revise.",basis="https://confluence.ecmwf.int/pages/viewpage.action?pageId=177484060"))
        outputs.append({"id": Path(filename).stem, "name": f"{selection.product.name} {item['date']} {item['variable']}", "category": "climate", "kind": "climate", "role": "native_cf_export",
                        "file": filename, "sha256": file_hash(workdir/filename), "scientific_hash": result["scientific_hash"], "product": selection.product.model_dump(mode="json"), "parameters": one.parameters,
                        "recipe": {"operation": "Preserve native values and calibration; add CF time bounds and AOI masks", "source_sha256":receipt.sha256}})
    return outputs, results, findings


@contextmanager
def climate_member(source,workdir,cancelled):
    import zipfile
    if not zipfile.is_zipfile(source):
        yield source
        return
    from .national import inspect_archive
    members=inspect_archive(source,cancelled)
    scientific=[name for name in members if name.lower().endswith(('.nc','.nc4','.grib','.grb'))]
    if len(scientific)!=1 or not scientific[0].lower().endswith(('.nc','.nc4')):
        raise ValueError('A single-variable CDS request returned an ambiguous or unsupported scientific member inventory')
    with tempfile.TemporaryDirectory(dir=workdir) as directory,zipfile.ZipFile(source) as archive:
        path=Path(directory)/'source.nc'
        with archive.open(scientific[0]) as stream,path.open('wb') as output:
            while block:=stream.read(65536):
                if cancelled(): raise Cancelled('Cancelled during climate archive materialization')
                output.write(block)
        yield path
