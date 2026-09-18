"""Sentinel-2 calibration is read from the retained SAFE product metadata."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET

BANDS = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B10", "B11", "B12"]
BASIS = "https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_L2A_baseline.html"


def calibration(raw, expected_baseline, bands):
    root = ET.fromstring(raw)
    def nodes(name):
        return [node for node in root.iter() if node.tag.split("}")[-1] == name]
    def single(name):
        values = {node.text.strip() for node in nodes(name) if node.text}
        if len(values) != 1:
            raise ValueError("Missing or ambiguous Sentinel-2 "+name)
        return next(iter(values))
    baseline = single("PROCESSING_BASELINE")
    if baseline != expected_baseline:
        raise ValueError("Sentinel-2 processing baseline differs from discovery")
    quantum = float(single("BOA_QUANTIFICATION_VALUE"))
    if not math.isfinite(quantum) or quantum <= 0:
        raise ValueError("Invalid Sentinel-2 quantification value")
    offsets = {}
    for node in nodes("BOA_ADD_OFFSET"):
        index = int(node.attrib["band_id"])
        if index < 0 or index >= len(BANDS) or BANDS[index] in offsets:
            raise ValueError("Invalid or duplicate Sentinel-2 calibration band")
        offsets[BANDS[index]] = float(node.text)
    result = {}
    for band in bands:
        if float(baseline) >= 4 and band not in offsets:
            raise ValueError("Missing mandatory Sentinel-2 band calibration offset")
        offset = offsets.get(band, 0.)
        if not math.isfinite(offset):
            raise ValueError("Nonfinite Sentinel-2 calibration")
        result[band] = {"scale": 1/quantum, "offset": offset/quantum, "quantification": quantum, "encoded_offset": offset,
                        "processing_baseline": baseline, "formula": "reflectance = (DN + BOA_ADD_OFFSET) / BOA_QUANTIFICATION_VALUE",
                        "nodata": 0, "units": "1", "basis": BASIS, "values": "encoded DN preserved"}
    return result


def validate_registration(dataset, asset):
    from pyproj import CRS
    metadata = asset.metadata
    properties, descriptor = metadata["properties"], metadata["asset"]
    expected = CRS.from_user_input(properties.get("proj:epsg") or descriptor.get("proj:code"))
    if not CRS.from_wkt(dataset.GetProjection()).equals(expected):
        raise ValueError("Imagery CRS differs from the frozen STAC item")
    if [dataset.RasterYSize, dataset.RasterXSize] != descriptor.get("proj:shape"):
        raise ValueError("Imagery dimensions differ from the frozen STAC asset")
    affine = descriptor.get("proj:transform")
    if not affine or len(affine) < 6:
        raise ValueError("Imagery asset lacks required grid registration metadata")
    expected_grid = [affine[2], affine[0], affine[1], affine[5], affine[3], affine[4]]
    if any(abs(a-b) > 1e-8 for a,b in zip(dataset.GetGeoTransform(), expected_grid)):
        raise ValueError("Imagery grid registration differs from discovery")
    if dataset.RasterCount != 1:
        raise ValueError("An explicitly selected imagery band must contain one band")
