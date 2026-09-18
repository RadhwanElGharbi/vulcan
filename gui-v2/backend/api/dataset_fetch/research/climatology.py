"""Lossless WorldClim monthly-grid export with CF climatological coordinates."""
from datetime import datetime
import hashlib

import numpy as np

from .contracts import canonical, file_hash
from .netcdf_compat import load_netcdf
from .transport import Cancelled


def export_climatology(source_path, mask_path, output, product, source_hash, cancelled=lambda: False):
    netCDF4 = load_netcdf()
    from osgeo import gdal
    from pyproj import CRS
    source, masks = gdal.Open(str(source_path)), gdal.Open(str(mask_path))
    if source.RasterCount != 12 or not CRS.from_wkt(source.GetProjection()).equals(CRS.from_epsg(4326)):
        raise ValueError('Climatology export requires 12 native geographic monthly bands')
    gt = source.GetGeoTransform()
    if gt[2] or gt[4]:
        raise ValueError('Unsupported rotated climatology grid')
    variable = product.semantics['variable']
    methods = 'time: sum within years time: mean over years' if variable == 'prec' else 'time: mean within years time: mean over years'
    units = 'degree_Celsius' if product.units == 'degC' else product.units
    time_units, calendar = 'days since 1970-01-01 00:00:00', 'proleptic_gregorian'
    bounds = [[datetime(1970,m,1), datetime(2000 if m<12 else 2001,m+1 if m<12 else 1,1)] for m in range(1,13)]
    time_bounds = netCDF4.date2num(np.asarray(bounds),time_units,calendar=calendar)
    times = netCDF4.date2num([datetime(2000,m,15) for m in range(1,13)],time_units,calendar=calendar)
    nx,ny = source.RasterXSize,source.RasterYSize
    dtype = source.GetRasterBand(1).ReadAsArray(0,0,1,1).dtype
    nodata = source.GetRasterBand(1).GetNoDataValue()
    with netCDF4.Dataset(output,'w',format='NETCDF4') as target:
        target.setncatts({'Conventions':'CF-1.12','title':product.name,'source':product.endpoint,'references':' '.join(product.assessment.documentation),
                         'institution':product.publisher,'license':product.license,'source_scientific_sha256':source_hash,
                         'history':'ZEUS: retained twelve native monthly grids and AOI validity masks; added CF climatological coordinates without resampling.',
                         'comment':'Monthly 1970–2000 climatology on the unchanged native grid. The AOI mask is ancillary; this is not a sequence of observations in 2000.'})
        for name,size in [('time',12),('latitude',ny),('longitude',nx),('bounds',2)]:
            target.createDimension(name,size)
        time = target.createVariable('time','f8',('time',),fill_value=False)
        time.setncatts({'standard_name':'time','units':time_units,'calendar':calendar,'units_metadata':'leap_seconds: none','climatology':'climatology_bounds','axis':'T'})
        time[:] = times
        target.createVariable('climatology_bounds','f8',('time','bounds'),fill_value=False)[:] = time_bounds
        for name,size,origin,step,unit,axis in [('longitude',nx,gt[0],gt[1],'degrees_east','X'),('latitude',ny,gt[3],gt[5],'degrees_north','Y')]:
            coord = target.createVariable(name,'f8',(name,),fill_value=False)
            coord.setncatts({'standard_name':name,'units':unit,'axis':axis,'bounds':name+'_bounds'})
            coord[:] = origin+(np.arange(size)+.5)*step
            target.createVariable(name+'_bounds','f8',(name,'bounds'),fill_value=False)[:] = origin+(np.arange(size)[:,None]+np.array([0,1]))*step
        grid = target.createVariable('crs','i4')
        grid.setncatts(CRS.from_epsg(4326).to_cf())
        grid.assignValue(0)
        values = target.createVariable(variable,dtype,('time','latitude','longitude'),fill_value=nodata if nodata is not None else False,zlib=True,complevel=4,chunksizes=(1,min(128,ny),min(256,nx)))
        values.setncatts({'units':units,'long_name':product.name,'cell_methods':methods,'grid_mapping':'crs','ancillary_variables':'aoi_validity'})
        if variable in ('tmin','tmax','tavg'):
            values.units_metadata='temperature: on_scale'
        validity = target.createVariable('aoi_validity','u1',('time','latitude','longitude'),fill_value=False,zlib=True)
        validity.setncatts({'long_name':'AOI validity mask','flag_values':np.array([0,1,255],dtype='uint8'),'flag_meanings':'valid missing_or_invalid outside_aoi'})
        values.set_auto_maskandscale(False)
        for month in range(12):
            band = source.GetRasterBand(month+1)
            if (band.GetScale() not in (None,1) or band.GetOffset() not in (None,0)):
                raise ValueError('WorldClim has an unexpected calibration transform')
            for y in range(0,ny,128):
                if cancelled(): raise Cancelled('Cancelled during CF export')
                count = min(128,ny-y)
                values[month,y:y+count,:] = band.ReadAsArray(0,y,nx,count)
                validity[month,y:y+count,:] = masks.GetRasterBand(month+1).ReadAsArray(0,y,nx,count)
    # Decode CF, reconcile all time coordinates, and compare every exported value.
    import xarray as xr
    scientific = hashlib.sha256(canonical({'source':source_hash,'variable':variable,'units':units,'cell_methods':methods,'calendar':calendar,'time_units_metadata':'leap_seconds: none','temperature_units_metadata':'temperature: on_scale' if variable in ('tmin','tmax','tavg') else None,'time':times.tolist(),'climatology_bounds':time_bounds.tolist()}))
    with xr.open_dataset(output,decode_cf=True,mask_and_scale=False) as check:
        if check.sizes['time'] != 12 or check.time.attrs['climatology'] != 'climatology_bounds' or check[variable].attrs['units'] != units:
            raise ValueError('CF climatology metadata did not round-trip')
        for month in range(12):
            for y in range(0,ny,128):
                if cancelled(): raise Cancelled('Cancelled during CF validation')
                count = min(128,ny-y)
                actual = check[variable].isel(time=month,latitude=slice(y,y+count)).values
                expected = source.GetRasterBand(month+1).ReadAsArray(0,y,nx,count)
                mask = check.aoi_validity.isel(time=month,latitude=slice(y,y+count)).values
                if not np.array_equal(actual,expected,equal_nan=True) or not np.array_equal(mask,masks.GetRasterBand(month+1).ReadAsArray(0,y,nx,count)):
                    raise ValueError('CF export changed native scientific values or masks')
                scientific.update(actual.astype(dtype.newbyteorder('<')).tobytes())
                scientific.update(mask.astype('uint8').tobytes())
    return {'scientific_hash':scientific.hexdigest(),'kind':'climate','months':12,'native_values_preserved':True,
            'artifact':output.name,'rule_results':[{'rule':'cf-climatological-coordinates','status':'passed','basis':'https://cfconventions.org/Data/cf-conventions/cf-conventions-1.12/cf-conventions.html#climatological-statistics'},
             {'rule':'complete-native-value-mask-roundtrip','status':'passed','basis':'ZEUS policy: complete-extract/1.0'}]}
