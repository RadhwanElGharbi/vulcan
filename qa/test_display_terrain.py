"""Offline checks for display terrain; background must never leak into DEM measurements."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from osgeo import gdal, osr
gdal.UseExceptions()

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gui-v2/backend'))
from api.data import render_terrain_tile, mercator_tile_bounds, encode_mapbox_terrain

class TerrainTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'dem.tif'
        self.bounds=mercator_tile_bounds(3,4,3)
        w,s,e,n=self.bounds
        self.ds=gdal.GetDriverByName('GTiff').Create(str(self.path),256,256,1,gdal.GDT_Float32)
        self.ds.SetGeoTransform((w,(e-w)/256,0,n,0,-(n-s)/256))
        crs=osr.SpatialReference();crs.ImportFromEPSG(3857);self.ds.SetProjection(crs.ExportToWkt())
        self.band=self.ds.GetRasterBand(1);self.band.SetNoDataValue(-32768)

    def tearDown(self):
        self.band=None;self.ds=None;self.directory.cleanup()

    def render(self,values,**kwargs):
        self.band.WriteArray(values);self.band.FlushCache();self.ds.FlushCache()
        return np.asarray(Image.open(io.BytesIO(render_terrain_tile(self.path,3,4,3,**kwargs))))

    def test_nodata_hole_and_valid_zero(self):
        values=np.zeros((256,256),dtype=np.float32);values[64:128,64:128]=-32768
        rgba=self.render(values)
        self.assertEqual(rgba[90,90,3],0)
        self.assertEqual(rgba[20,20,3],255)
        pixel=rgba[20,20].astype(float)
        self.assertAlmostEqual(-10000+(pixel[0]*65536+pixel[1]*256+pixel[2])*.1,0)

    def test_frozen_aoi_with_hole(self):
        aoi={'type':'Polygon','coordinates':[[[2,2],[40,2],[40,35],[2,35],[2,2]],[[10,10],[10,20],[20,20],[20,10],[10,10]]]}
        rgba=self.render(np.full((256,256),150,dtype=np.float32),aoi_json=json.dumps(aoi))
        self.assertEqual(rgba[0,0,3],0)
        self.assertGreater(np.count_nonzero(rgba[:,:,3]),0)
        import math
        x=int(15/45*256);y=int((1-math.asinh(math.tan(math.radians(15)))/math.pi)/2*8*256)-3*256
        self.assertEqual(rgba[y,x,3],0)

    def test_scale_offset_and_feet(self):
        self.band.SetScale(2);self.band.SetOffset(10);self.band.SetUnitType('ft')
        rgba=self.render(np.full((256,256),100,dtype=np.float32))
        pixel=rgba[128,128].astype(float)
        self.assertAlmostEqual(-10000+(pixel[0]*65536+pixel[1]*256+pixel[2])*.1,64.0,places=1)

    def test_retained_gap_mask(self):
        path=Path(self.directory.name)/'gap.tif'
        gap=gdal.GetDriverByName('GTiff').Create(str(path),256,256,1,gdal.GDT_Byte)
        gap.SetGeoTransform(self.ds.GetGeoTransform());gap.SetProjection(self.ds.GetProjection())
        values=np.zeros((256,256),dtype=np.uint8);values[60:120,60:120]=1
        gap.GetRasterBand(1).SetNoDataValue(255);gap.GetRasterBand(1).WriteArray(values);gap=None
        rgba=self.render(np.full((256,256),150,dtype=np.float32),gap_mask=str(path))
        self.assertEqual(rgba[90,90,3],0)
        self.assertEqual(rgba[20,20,3],255)

    def test_nonfinite_has_no_coverage(self):
        rgba=encode_mapbox_terrain(np.array([[np.nan,np.inf,-np.inf,0]]),-32768)
        self.assertEqual(rgba[0,:,3].tolist(),[0,0,0,255])

    def test_outside_raster_is_transparent(self):
        self.band.Fill(100);self.band.FlushCache();self.ds.FlushCache()
        rgba=np.asarray(Image.open(io.BytesIO(render_terrain_tile(self.path,3,1,1))))
        self.assertEqual(np.count_nonzero(rgba[:,:,3]),0)

if __name__=='__main__':unittest.main()
