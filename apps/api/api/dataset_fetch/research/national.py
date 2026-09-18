from __future__ import annotations

import json
import zipfile
import stat
import time
from pathlib import Path, PurePosixPath

from .contracts import Asset


def discover_nhn(product, aoi, transport):
    from osgeo import ogr, osr
    index = transport.download(transport.inspect(Asset(id="nhn-index", url=product.endpoint, filename="index.zip", role="index")))
    path = transport.store.verify_blob(index.sha256)
    with zipfile.ZipFile(path) as archive:
        layers = sorted(n for n in archive.namelist() if n.lower().endswith(".shp"))
    if len(layers) != 1:
        raise ValueError("NHN index has an ambiguous layer inventory")
    ds = ogr.Open("/vsizip/{"+path.as_posix()+"}/"+layers[0])
    if ds is None:
        raise ValueError("NHN index is unreadable")
    layer = ds.GetLayer(0)
    src = osr.SpatialReference()
    src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = layer.GetSpatialRef()
    if dst is None:
        raise ValueError("NHN index CRS is missing")
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    from .projection import resolve_operation
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    operation=resolve_operation(4326,dst.ExportToWkt(),aoi,always_xy=True)
    polygon=ogr.CreateGeometryFromWkb(transform(Transformer.from_pipeline(operation['pipeline']).transform,shape(aoi)).wkb)
    layer.SetSpatialFilter(polygon)
    codes = sorted({f.GetField("DATASETNAM").lower() for f in layer})
    assets = []
    for code in codes:
        name = f"nhn_rhn_{code}_shp_en.zip"
        url = f"https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/shp_en/{code[:2]}/{name}"
        assets.append(transport.inspect(Asset(id=code, url=url, filename=name)))
    return assets, [{"receipt": index.model_dump(mode="json"), "selected_workunits": codes,'index_aoi_operation':operation}], []


def inspect_archive(path, cancelled=lambda:False):
    """Check the full retained container before GDAL reads any scientific member."""
    started = time.monotonic()
    with zipfile.ZipFile(path) as archive:
        records = archive.infolist()
        members = sorted(record.filename for record in records if not record.is_dir())
        if len(records)>100_000 or sum(r.file_size for r in records)>100*1024**3:
            raise ValueError('Archive exceeds the member or expanded-byte limit')
        if len({n.casefold() for n in members}) != len(members):
            raise ValueError('Archive has duplicate or ambiguous member names')
        for record in records:
            name = record.filename
            if '\\' in name or ':' in name or name.startswith('/') or '..' in PurePosixPath(name).parts or stat.S_ISLNK(record.external_attr >> 16) or record.flag_bits & 1:
                raise ValueError('Unsafe or encrypted archive member')
            if record.is_dir(): continue
            with archive.open(record) as source:
                header = source.read(16)
                check_signature(name,header)
                while source.read(65536):
                    from .transport import Cancelled
                    if cancelled(): raise Cancelled('Cancelled during archive validation')
                    if time.monotonic()-started>3600: raise ValueError('Archive validation exceeded its deadline')
        names = {n.casefold() for n in members}
        for name in members:
            if name.lower().endswith('.shp'):
                if any(name[:-4].casefold()+suffix not in names for suffix in ('.shx','.dbf','.prj')):
                    raise ValueError('Shapefile archive is missing a mandatory index, attributes or CRS dependency')
    return members


def check_signature(name, header):
    if name.lower().endswith(('.tif','.tiff')) and header[:4] not in (b'II*\x00',b'MM\x00*',b'II+\x00',b'MM\x00+'):
        raise ValueError('Raster object is not a TIFF; external virtual dependencies are not permitted')
    if name.lower().endswith('.gpkg') and header != b'SQLite format 3\x00':
        raise ValueError('GeoPackage object has an invalid container signature')


def archive_sources(path, adapter, cancelled=lambda:False):
    members = inspect_archive(path,cancelled)
    if adapter == "nhn":
        selected = [n for n in members if n.lower().endswith(".shp") and "nlflow" in n.lower()]
    else:
        selected = [n for n in members if n.lower().endswith((".tif", ".tiff", ".gpkg", ".shp"))]
        geodatabases = {str(parent) for name in members for parent in PurePosixPath(name).parents if parent.suffix.lower()=='.gdb'}
        selected.extend(sorted(geodatabases))
    if not selected:
        raise ValueError("Archive lacks the required scientific layer")
    return ["/vsizip/"+path.as_posix()+"/"+n for n in selected]
