from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import FetchContext
from .utils import (
    _bbox_from_wgs84_extent,
    _bbox_wgs84_covers_target,
    _bbox_wgs84_within_target,
    _extract_epsg_from_info,
    _extract_raster_statistics,
    _gdal_info,
    _ogr_info,
    _status_from_issues,
    _vector_epsg,
    _vector_feature_count,
)


def _validate_raster_file(
    path: Path,
    ctx: FetchContext,
    *,
    expect_epsg: Optional[str] = None,
    require_covers_aoi_bbox: bool = False,
    require_within_aoi_bbox: bool = False,
) -> Tuple[str, List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if not path.exists():
        errors.append("File does not exist")
        return _status_from_issues(errors, warnings), errors, warnings

    if path.stat().st_size < 1024:
        errors.append(f"File too small (<1KB): {path.name}")
        return _status_from_issues(errors, warnings), errors, warnings

    info = _gdal_info(path)
    if not info:
        errors.append("GDAL could not open raster (gdalinfo failed)")
        return _status_from_issues(errors, warnings), errors, warnings

    epsg = _extract_epsg_from_info(info)
    if not epsg:
        errors.append("CRS/EPSG could not be determined from GDAL metadata")
    elif expect_epsg and epsg != expect_epsg:
        errors.append(f"Unexpected CRS: {epsg} (expected {expect_epsg})")

    wgs84 = info.get("wgs84Extent")
    bbox_wgs84: Optional[Dict[str, float]] = None
    if isinstance(wgs84, dict):
        bbox_wgs84 = _bbox_from_wgs84_extent(wgs84)

    if require_covers_aoi_bbox:
        if bbox_wgs84 is None:
            warnings.append("wgs84Extent unavailable; cannot confirm AOI bbox coverage")
        elif not _bbox_wgs84_covers_target(bbox_wgs84, ctx.bbox):
            errors.append("Raster bbox does not fully cover AOI bbox (may be incomplete download)")

    if require_within_aoi_bbox:
        if bbox_wgs84 is None:
            warnings.append("wgs84Extent unavailable; cannot confirm raster is within AOI bbox")
        elif not _bbox_wgs84_within_target(bbox_wgs84, ctx.bbox, tol=1e-4):
            warnings.append("Raster bbox extends beyond AOI bbox (may include extra padding)")

    stats = _extract_raster_statistics(info) or {}
    if stats:
        try:
            if "min" in stats and "max" in stats and abs(float(stats["min"]) - float(stats["max"])) < 1e-12:
                warnings.append("Raster contains a single valid value; inspect source coverage for this AOI")
        except Exception:  # noqa: BLE001
            warnings.append("Could not evaluate raster min/max for constant-value check")
        try:
            if float(stats.get("valid_percent", 100.0)) <= 0.0:
                errors.append("Raster has 0% valid pixels (all NoData)")
        except Exception:  # noqa: BLE001
            pass
    else:
        warnings.append("Raster statistics missing (cannot verify non-constant / non-NoData quickly)")

    return _status_from_issues(errors, warnings), errors, warnings


def _validate_vector_file(
    path: Path,
    ctx: FetchContext,
    *,
    expect_epsg: Optional[str] = None,
    require_nonempty: bool = False,
) -> Tuple[str, List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if not path.exists():
        errors.append("File does not exist")
        return _status_from_issues(errors, warnings), errors, warnings

    if path.stat().st_size < 1024:
        errors.append(f"File too small (<1KB): {path.name}")
        return _status_from_issues(errors, warnings), errors, warnings

    info = _ogr_info(path)
    if not info:
        errors.append("OGR could not open vector (ogrinfo failed)")
        return _status_from_issues(errors, warnings), errors, warnings

    epsg = _vector_epsg(path)
    if expect_epsg and epsg and epsg != expect_epsg:
        errors.append(f"Unexpected CRS: {epsg} (expected {expect_epsg})")
    elif expect_epsg and not epsg:
        warnings.append("CRS/EPSG could not be determined for vector; cannot confirm target CRS")

    feature_count = _vector_feature_count(info)
    if feature_count is None:
        warnings.append("Feature count unavailable from OGR metadata")
    elif feature_count <= 0:
        if require_nonempty:
            errors.append("Vector has 0 features (unexpected for this category)")
        else:
            warnings.append("Vector has 0 features (may indicate no coverage for AOI)")

    return _status_from_issues(errors, warnings), errors, warnings


def _domain_warnings_raster(path: Path, category: str, ctx: FetchContext) -> List[str]:
    """Domain-specific warning checks for raster datasets."""
    warnings: List[str] = []
    info = _gdal_info(path)
    if not info:
        return warnings

    stats = _extract_raster_statistics(info) or {}

    if category == "dem":
        min_val = stats.get("min")
        max_val = stats.get("max")
        if min_val is not None and max_val is not None:
            try:
                if float(min_val) < -500:
                    warnings.append(f"DEM has unusually low elevation ({float(min_val):.1f}m) — verify no ocean/void artifacts")
                if float(max_val) > 9000:
                    warnings.append(f"DEM has unusually high elevation ({float(max_val):.1f}m) — verify data integrity")
                rng = float(max_val) - float(min_val)
                if rng < 0.01:
                    warnings.append("DEM appears flat (near-zero elevation range) — possible constant-value artifact")
            except (TypeError, ValueError):
                pass

    return warnings


def _domain_warnings_vector(path: Path, category: str, ctx: FetchContext) -> List[str]:
    """Domain-specific warning checks for vector datasets."""
    warnings: List[str] = []
    info = _ogr_info(path)
    if not info:
        return warnings

    count = _vector_feature_count(info)
    if count is not None:
        if category in ("roads", "waterways") and count < 10:
            warnings.append(f"Unusually low feature count ({count}) for {category} — verify AOI coverage")

    return warnings

