"""Full native ERA5 AOI/time coverage and a CF export with explicit time bounds."""
from __future__ import annotations

import shutil
from pathlib import Path
import numpy as np

from .contracts import digest, file_hash
from .netcdf_compat import load_netcdf
from .validation import validate_raster
from .transport import Cancelled

BASIS = 'https://confluence.ecmwf.int/pages/viewpage.action?pageId=348786747'


def export_climate(source, destination, selection, plan, day, cancelled=lambda:False):
    import xarray as xr
    from osgeo import gdal, osr
    from .climate import VARIABLES
    records, findings, outputs = [], [], []
    with xr.open_dataset(source, engine='netcdf4') as data:
        time_name = 'valid_time' if 'valid_time' in data else 'time'
        lon, lat = np.asarray(data.longitude.values), np.asarray(data.latitude.values)
        if min(len(lon),len(lat)) < 1:
            raise ValueError('Climate native grid has empty coordinates')
        expected=selection.product.semantics['provider_grid']
        dx = float(lon[1]-lon[0]) if len(lon)>1 else expected[0]
        dy = float(lat[1]-lat[0]) if len(lat)>1 else -expected[1]
        if not np.allclose([abs(dx),abs(dy)],expected,rtol=0,atol=1e-10):
            raise ValueError('Climate native spacing differs from the frozen provider grid')
        if not np.allclose(np.diff(lon),dx,rtol=0,atol=1e-10) or not np.allclose(np.diff(lat),dy,rtol=0,atol=1e-10):
            raise ValueError('Climate native grid is irregular; no implicit regridding is permitted')
        angular_lon = lon-360 if np.all(lon>180) else lon
        if angular_lon.min() < -180 or angular_lon.max()>180:
            raise ValueError('Climate longitude wrap requires a separately partitioned native export')
        grid = (float(angular_lon[0]-dx/2),dx,0,float(lat[0]-dy/2),0,dy)
        reference=osr.SpatialReference(); reference.ImportFromEPSG(4326)
        for requested in selection.parameters['variables']:
            name, units = VARIABLES[requested]
            field=data[name].transpose(time_name,'latitude','longitude')
            path=destination.with_name(destination.stem+'-'+name+'.coverage.tif')
            raster=gdal.GetDriverByName('GTiff').Create(str(path),len(lon),len(lat),24,gdal.GDT_Float64,options=['TILED=YES','COMPRESS=LZW'])
            raster.SetProjection(reference.ExportToWkt()); raster.SetGeoTransform(grid)
            for t in range(24):
                band=raster.GetRasterBand(t+1); band.SetNoDataValue(float('nan')); band.SetUnitType(units)
                band.SetDescription(f'{day}T{t:02d}:00:00Z')
                for y in range(0,len(lat),128):
                    if cancelled(): raise Cancelled('Cancelled during climate spatial validation')
                    block=np.asarray(field.isel({time_name:t,'latitude':slice(y,y+128)}).values)
                    band.WriteArray(block,0,y)
            raster=None
            product=selection.product.model_copy(deep=True)
            product.units=units
            product.semantics={**product.semantics,'expected_bands':24,'value_range': [0,None] if requested in ('2m_temperature','total_precipitation','surface_pressure') else None}
            gaps=path.with_suffix('.gaps.tif')
            result, issues=validate_raster(path,plan.aoi,product,selection_id=f'{selection.id}:{day}:{name}',cancelled=cancelled,gap_path=gaps,recipe=selection.recipe)
            result.update({'variable':requested,'selection_id':selection.id,'artifact':path.name,'time_steps':[f'{day}T{t:02d}:00:00Z' for t in range(24)],'gap_mask':gaps.name})
            records.append(result); findings.extend(issues)
            outputs.append({'id':path.stem,'name':f'{requested} {day}: hourly coverage evidence','category':'climate','kind':'climate','role':'validation_derivative',
                            'file':path.name,'sha256':file_hash(path),'scientific_hash':result['scientific_hash'],
                            'gap_mask':gaps.name,'gap_sha256':file_hash(gaps),'extent_gap':gaps.with_suffix('.geojson').name,'extent_gap_sha256':file_hash(gaps.with_suffix('.geojson')),
                            'product':selection.product.model_dump(mode='json'),'parameters':selection.parameters,
                            'recipe':{'operation':'Native decoded values copied to temporal raster for AOI validation; no resampling','grid':grid,'angular_reference':'longitude/latitude degrees; original provider Earth model remains in the retained response'}})
    # Copy the original data object; adding CF descriptions cannot rewrite its values.
    shutil.copyfile(source,destination)
    netcdf=load_netcdf()
    with netcdf.Dataset(destination,'a') as exported:
        if 'zeus_bounds' in exported.dimensions: raise ValueError('Reserved climate export dimension already exists')
        exported.createDimension('zeus_bounds',2)
        time=exported[time_name]
        for coordinate in (time_name,'latitude','longitude'):
            axis=exported[coordinate]
            values=axis[:]
            if np.any(np.ma.getmaskarray(values)) or not np.all(np.isfinite(values)):
                raise ValueError('CF coordinate axes must contain complete finite values')
            # CF forbids missing-value declarations on coordinate variables.
            # The retained provider object preserves the original declarations.
            for attribute in ('_FillValue','missing_value'):
                if attribute in axis.ncattrs():axis.delncattr(attribute)
        time.standard_name='time'; time.axis='T'
        calendar=getattr(time,'calendar','standard')
        time.calendar=calendar
        time.units_metadata=getattr(time,'units_metadata','leap_seconds: unknown')
        times=netcdf.num2date(time[:],time.units,calendar=calendar)
        from datetime import timedelta
        bounds=np.asarray([[netcdf.date2num(t-timedelta(hours=1),time.units,calendar=calendar),netcdf.date2num(t,time.units,calendar=calendar)] for t in times])
        existing=getattr(time,'bounds',None)
        accumulating='total_precipitation' in selection.parameters['variables']
        if existing:
            if existing not in exported.variables:
                raise ValueError('Provider references missing time bounds')
            supplied=np.asarray(exported[existing][:])
            if accumulating and not np.array_equal(supplied,bounds):
                raise ValueError('Provider time bounds contradict the ERA5 hourly accumulation contract')
            if supplied.shape!=(len(times),2) or not np.all(np.isfinite(supplied)) or np.any(supplied[:,0]>time[:]) or np.any(supplied[:,1]<time[:]):
                raise ValueError('Provider time bounds do not contain their coordinate times')
        if accumulating and not existing:
            variable=exported.createVariable('zeus_time_bounds','f8',(time_name,'zeus_bounds'),fill_value=False)
            variable[:]=bounds
            time.bounds='zeus_time_bounds'
        for coordinate,axis,delta in [('longitude','X',dx),('latitude','Y',dy)]:
            variable=exported[coordinate]; variable.standard_name=coordinate; variable.axis=axis
            centres=np.asarray(variable[:]); expected_bounds=np.stack([centres-delta/2,centres+delta/2],axis=1)
            existing=getattr(variable,'bounds',None)
            if existing:
                if existing not in exported.variables or np.asarray(exported[existing][:]).shape!=expected_bounds.shape or not np.allclose(np.sort(exported[existing][:],axis=1),np.sort(expected_bounds,axis=1),rtol=0,atol=1e-10):
                    raise ValueError('Provider horizontal bounds contradict native grid registration')
            else:
                b=exported.createVariable('zeus_'+coordinate+'_bounds','f8',(coordinate,'zeus_bounds'))
                b[:]=expected_bounds
                variable.bounds=b.name
        for requested,record,output in zip(selection.parameters['variables'],records,outputs):
            name,_=VARIABLES[requested]
            field=exported[name]
            method=f'{time_name}: sum' if requested=='total_precipitation' else f'{time_name}: point'
            if getattr(field,'cell_methods',method) != method:
                raise ValueError('Provider cell methods contradict the explicit ERA5 temporal contract')
            field.cell_methods=method
            if requested=='2m_temperature':
                field.units_metadata=getattr(field,'units_metadata','temperature: on_scale')
            field.zeus_temporal_basis=BASIS
            field.zeus_time_meaning='one-hour accumulation ending at coordinate time' if requested=='total_precipitation' else 'instantaneous value at coordinate time'
            mask=exported.createVariable('zeus_'+name+'_aoi_validity','u1',(time_name,'latitude','longitude'),fill_value=255,zlib=True)
            mask.flag_values=np.asarray([0,1],dtype='u1'); mask.flag_meanings='valid missing_or_invalid'; mask.long_name='AOI validity; fill value denotes outside AOI'
            raster=gdal.Open(str(destination.parent/output['gap_mask']))
            for t in range(24):
                for y in range(0,len(lat),128):
                    if cancelled(): raise Cancelled('Cancelled during CF mask export')
                    mask[t,y:y+128,:]=raster.GetRasterBand(t+1).ReadAsArray(0,y,len(lon),min(128,len(lat)-y))
            raster=None
            field.ancillary_variables=' '.join(filter(None,[getattr(field,'ancillary_variables',''),mask.name]))
        exported.Conventions='CF-1.12'
        exported.history='\n'.join(filter(None,[getattr(exported,'history',''),'ZEUS: retained native data and calibration; added coordinate bounds, explicit temporal semantics and AOI validity masks without resampling. Removed prohibited missing-value declarations from verified complete coordinate axes.']))
        exported.title=getattr(exported,'title',selection.product.name)
        exported.zeus_source_sha256=file_hash(source)
        exported.zeus_recipe='Native values, coordinates and calibration retained; explicit coordinate bounds, temporal semantics and AOI masks added. Missing-value declarations removed from verified complete coordinate axes for CF conformance.'
    # Compare every decoded value after export, in bounded blocks.
    with xr.open_dataset(source,engine='netcdf4') as original, xr.open_dataset(destination,engine='netcdf4') as exported:
        for requested in selection.parameters['variables']:
            name,_=VARIABLES[requested]
            for t in range(24):
                for y in range(0,len(lat),128):
                    index={time_name:t,'latitude':slice(y,y+128)}
                    if not np.array_equal(original[name].isel(index).values,exported[name].isel(index).values,equal_nan=True):
                        raise ValueError('CF export changed native scientific values')
    return records,findings,outputs
