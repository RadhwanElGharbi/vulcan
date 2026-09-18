"""Bounded VRT subsets with retained publisher checksum inventories."""
import math
from pathlib import PurePosixPath
import re
from urllib.parse import urljoin,urlsplit
import xml.etree.ElementTree as ET

from .contracts import Asset,Finding


def checksums(content):
    records={}
    if len(content)>16*1024**2:raise ValueError('Publisher checksum inventory exceeds its limit')
    for line in content.decode('ascii').splitlines():
        if not line.strip():continue
        match=re.fullmatch(r'([0-9a-fA-F]{64}) [ *]([^\\/:]+)',line)
        if not match:raise ValueError('Malformed publisher SHA-256 inventory')
        sha,name=match.groups()
        if name in records or name in ('.','..'):raise ValueError('Duplicate or unsafe publisher checksum identity')
        records[name]=sha.lower()
    if not records:raise ValueError('Empty publisher checksum inventory')
    return records


def vrt_contract(content,stem):
    if b'<!DOCTYPE' in content or b'<!ENTITY' in content:raise ValueError('External VRT declarations are not allowed')
    tree=ET.fromstring(content)
    if tree.tag!='VRTDataset' or set(tree.attrib)!={'rasterXSize','rasterYSize'} or any(c.tag not in ('SRS','GeoTransform','Metadata','VRTRasterBand') for c in tree):
        raise ValueError('Unsupported SoilGrids VRT structure or dependency')
    gt=[float(x) for x in tree.findtext('GeoTransform','').split(',')]
    bands=tree.findall('VRTRasterBand')
    if len(gt)!=6 or not all(math.isfinite(x) for x in gt) or gt[1:3]!=[250.,0.] or gt[4:]!=[0.,-250.] or not tree.findtext('SRS') or len(bands)!=1:
        raise ValueError('SoilGrids VRT violates the one-band 250 metre grid contract')
    from pyproj import CRS
    reference=CRS.from_user_input(tree.findtext('SRS'))
    if not reference.is_projected or any(axis.unit_conversion_factor!=1 for axis in reference.axis_info) or 'goode homolosine' not in reference.coordinate_operation.method_name.lower():
        raise ValueError('SoilGrids VRT CRS contradicts its documented metric Goode Homolosine grid')
    band=bands[0]
    if band.attrib!={'dataType':'Int16','band':'1'} or any(c.tag not in ('ComplexSource','SimpleSource','NoDataValue','Metadata','ColorInterp','Histograms') for c in band):
        raise ValueError('Unsupported SoilGrids VRT band or executable dependency')
    expected_nodata=float(band.findtext('NoDataValue'))
    if not math.isfinite(expected_nodata):raise ValueError('SoilGrids NoData is nonfinite')
    sources=[];seen=set()
    for parent in band:
        if parent.tag not in ('ComplexSource','SimpleSource'):continue
        if any(c.tag not in ('SourceFilename','SourceBand','SourceProperties','SrcRect','DstRect','NODATA') for c in parent):
            raise ValueError('Undocumented VRT source transformation or dependency')
        node=parent.find('SourceFilename');name=node.text if node is not None else ''
        if node is None or node.get('relativeToVRT')!='1' or not re.fullmatch(r'\./'+re.escape(stem)+r'/tileSG-\d{3}-\d{3}/tileSG-\d{3}-\d{3}_\d+-\d+\.tif',name or ''):
            raise ValueError('SoilGrids VRT references an unsupported source identity')
        if name in seen:raise ValueError('Duplicate SoilGrids VRT source identity')
        seen.add(name)
        props=parent.find('SourceProperties')
        src=parent.find('SrcRect');dst=parent.find('DstRect')
        if props is None or src is None or dst is None:raise ValueError('VRT source lacks complete grid registration')
        width,height=int(props.get('RasterXSize')),int(props.get('RasterYSize'))
        sx,sy,sw,sh=[int(src.get(k)) for k in ('xOff','yOff','xSize','ySize')]
        x,y,w,h=[float(dst.get(k)) for k in ('xOff','yOff','xSize','ySize')]
        if not all(math.isfinite(v) for v in (x,y,w,h)) or min(width,height,w,h,sw,sh)<=0 or min(x,y,sx,sy)<0 or sx+sw>width or sy+sh>height or props.get('DataType')!='Int16' or parent.findtext('SourceBand')!='1' or float(parent.findtext('NODATA',str(expected_nodata)))!=expected_nodata:
            raise ValueError('SoilGrids VRT implies invalid source registration or calibration')
        if x+w>int(tree.get('rasterXSize')) or y+h>int(tree.get('rasterYSize')):raise ValueError('VRT source extends beyond its declared grid')
        dx,dy=gt[1]*w/sw,gt[5]*h/sh
        sources.append({'filename':name,'rect':(x,y,w,h),'grid':{'width':width,'height':height,'affine':[gt[0]+x*gt[1]-sx*dx,dx,0,gt[3]+y*gt[5]-sy*dy,0,dy],
                        'window':[sx,sy,sw,sh],'nodata':expected_nodata,'datatype':'Int16'}})
    if not sources:raise ValueError('SoilGrids VRT contains no scientific sources')
    return tree.findtext('SRS'),gt,sources


def discover(product,params,aoi,transport):
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    from .projection import resolve_operation
    snapshots=[];inventories={}
    def inventory(directory):
        if directory not in inventories:
            receipt=transport.download(Asset(id=directory+'checksum.sha256.txt',url=directory+'checksum.sha256.txt',filename='checksums.txt',role='metadata'))
            inventories[directory]=checksums(transport.store.verify_blob(receipt.sha256).read_bytes())
            snapshots.append({'receipt':receipt.model_dump(mode='json'),'role':'publisher_sha256_inventory'})
        return inventories[directory]
    prop=product.semantics['property'];stem=f"{prop}_{params['depth']}_{params['statistic']}"
    base=product.endpoint+'/'+prop+'/'
    digest=inventory(base).get(stem+'.vrt')
    if not digest:raise ValueError('Publisher checksum inventory omits the requested VRT')
    url=base+stem+'.vrt'
    index=Asset(id=url,url=url,filename=stem+'.vrt',role='index',expected_hash=digest)
    receipt=transport.download(transport.inspect(index))
    srs,gt,sources=vrt_contract(transport.store.verify_blob(receipt.sha256).read_bytes(),stem)
    snapshots.extend([{'receipt':receipt.model_dump(mode='json')},{'soilgrids_vrt':receipt.sha256}])
    product.semantics['source_crs']=srs
    product.semantics['publisher_integrity']='SHA-256 checked against retained provider inventories for the VRT and every selected native tile'
    operation=resolve_operation(4326,srs,aoi,always_xy=True)
    snapshots.append({'index_aoi_operation':operation})
    bounds=transform(Transformer.from_pipeline(operation['pipeline']).transform,shape(aoi)).bounds
    px=sorted([(bounds[0]-gt[0])/gt[1],(bounds[2]-gt[0])/gt[1]])
    py=sorted([(bounds[1]-gt[3])/gt[5],(bounds[3]-gt[3])/gt[5]])
    assets=[];findings=[]
    product.semantics['vrt_interpretation']='Retained VRT establishes footprint, source precedence and native tile registration. Original tiles are each transformed once; any VRT upsampling is bypassed. Integer source windows are lossless subsets.'
    for ordinal,source in enumerate(sources):
        x,y,w,h=source['rect']
        if x>px[1]+2 or x+w<px[0]-2 or y>py[1]+2 or y+h<py[0]-2:continue
        url=urljoin(base,source['filename'])
        directory=url.rsplit('/',1)[0]+'/'
        filename=PurePosixPath(urlsplit(url).path).name
        sha=inventory(directory).get(filename)
        if not sha:raise ValueError('Publisher checksum inventory omits a selected native tile')
        assets.append(transport.inspect(Asset(id=f'{ordinal:08d}:'+url,url=url,filename=filename,expected_hash=sha,
            metadata={'vrt_source':source['filename'],'soilgrids_native_grid':source['grid']})))
        dx,dy=source['grid']['affine'][1],abs(source['grid']['affine'][5])
        if (dx,dy)!=(250,250):
            findings.append(Finding(id=f'native-grid:{ordinal}',rule='provider-native-resolution',severity='acknowledgement',basis=product.endpoint,
                message=f'The provider VRT places a {dx:g} × {dy:g} metre native tile on its nominal 250 metre grid. ZEUS retains that native tile and transforms it once; finer output spacing does not add source detail.',evidence=source))
    return assets,snapshots,findings


def validate_tiles(inputs,product):
    from osgeo import gdal
    from pyproj import CRS
    expected_crs=CRS.from_user_input(product.semantics['source_crs'])
    for asset,path in inputs:
        grid=asset.metadata['soilgrids_native_grid'];source=gdal.Open(path)
        if source is None or not source.GetProjection() or not CRS.from_wkt(source.GetProjection()).equals(expected_crs):
            raise ValueError('Native SoilGrids tile CRS contradicts its retained VRT')
        if source.RasterCount!=1 or (source.RasterXSize,source.RasterYSize)!=(grid['width'],grid['height']) or any(abs(a-b)>1e-6 for a,b in zip(source.GetGeoTransform(),grid['affine'])):
            raise ValueError('Native SoilGrids tile registration contradicts its retained VRT')
        band=source.GetRasterBand(1)
        if gdal.GetDataTypeName(band.DataType)!=grid['datatype'] or band.GetNoDataValue()!=grid['nodata'] or band.GetScale() not in (None,1) or band.GetOffset() not in (None,0):
            raise ValueError('Native SoilGrids tile encoding contradicts its retained VRT')
