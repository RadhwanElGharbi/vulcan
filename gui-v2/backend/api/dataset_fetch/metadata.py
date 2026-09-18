"""
Metadata generation for raw and processed datasets.

Extracts _generate_raw_metadata and _generate_processed_metadata from the
monolith dataset_fetch module.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .constants import DATASET_FETCH_PROTOCOL, PROTOCOL_VERSION
from .models import DatasetDefinition, FetchContext, _get_zeus_version
from .utils import (
    _bbox_dict_from_tuple,
    _bbox_from_wgs84_extent,
    _bbox_wgs84_from_tuple,
    _compute_file_sha256,
    _extract_epsg_from_info,
    _extract_raster_statistics,
    _extent_from_gdal_info,
    _gdal_info,
    _ogr_info,
    _utc_iso,
    _vector_bbox_wgs84,
    _vector_epsg,
    _vector_extent_dict,
    _vector_feature_count_ogr,
    _write_json,
)


def _generate_processed_metadata(
    defn: DatasetDefinition,
    ctx: FetchContext,
    raw_path: Path,
    raw_meta: Path,
    processed_path: Path,
    metadata_overrides: Optional[Dict[str, object]] = None,
    processed_meta_path: Optional[Path] = None,
) -> None:
    base_meta: Dict[str, Any] = {
        "dataset_name": f"{defn.label} (Processed)",
        "category": defn.key,
        "project": ctx.project,
        "processing_date": _utc_iso(),
        "target_crs": f"EPSG:{ctx.target_epsg}",
        "target_crs_name": ctx.target_crs_name,
        "data_type": "Raster" if defn.dataset_type == "raster" else "Vector",
        "format": "GeoTIFF" if defn.dataset_type == "raster" else "GeoPackage",
        "processed_path": str(processed_path),
        "raw_path": str(raw_path),
        "raw_metadata_file": str(raw_meta),
        "source_files": [
            {
                "filename": raw_path.name,
                "metadata": raw_meta.name if raw_meta.exists() else None,
            }
        ],
        "operations_applied": [
            {
                "operation": "reproject",
                "tool": "gdalwarp" if defn.dataset_type == "raster" else "ogr2ogr",
                "target_epsg": ctx.target_epsg,
            },
            {"operation": "clip", "tool": "AOI cutline", "source": str(ctx.cutline_path)},
        ],
        "file_size_bytes": processed_path.stat().st_size if processed_path.exists() else 0,
        "validation_status": "passed",
        "validation_date": _utc_iso(),
        "protocol_reference": DATASET_FETCH_PROTOCOL,
        "protocol_version": PROTOCOL_VERSION,
        "zeus_version": _get_zeus_version(),
        "notes": defn.description,
    }

    dataset_name_override: Optional[str] = None
    if metadata_overrides:
        overrides = copy.deepcopy(metadata_overrides)
        dataset_name_override = overrides.get("dataset_name")
        overrides.pop("tiles_downloaded", None)
        for key, value in overrides.items():
            if value is not None:
                base_meta[key] = value
    if dataset_name_override:
        base_meta["dataset_name"] = f"{dataset_name_override} (Processed)"

    info = _gdal_info(processed_path) if defn.dataset_type == "raster" else _ogr_info(processed_path)
    if info:
        if defn.dataset_type == "raster":
            geo = info.get("geoTransform")
            if geo and isinstance(geo, list) and len(geo) >= 6:
                base_meta["resolution_m"] = {
                    "x": abs(float(geo[1])),
                    "y": abs(float(geo[5])),
                }
            if "size" in info:
                base_meta["dimensions"] = {"width": info["size"][0], "height": info["size"][1]}
            extent = _extent_from_gdal_info(info)
            if extent:
                base_meta["extent"] = extent
            wgs84 = info.get("wgs84Extent")
            if isinstance(wgs84, dict):
                bbox = _bbox_from_wgs84_extent(wgs84)
                if bbox:
                    base_meta["bbox_wgs84"] = bbox
            stats = _extract_raster_statistics(info)
            if stats:
                base_meta["statistics"] = stats
        else:
            # For vectors, prefer OGR-based extent/count extraction; ogrinfo JSON output is inconsistent across GDAL versions.
            pass
    if defn.dataset_type == "vector":
        extent_native = _vector_extent_dict(processed_path)
        if extent_native:
            base_meta["extent"] = extent_native
        elif not base_meta.get("extent"):
            base_meta["extent"] = _bbox_dict_from_tuple(ctx.bbox)

        bbox = _vector_bbox_wgs84(processed_path)
        if bbox:
            base_meta["bbox_wgs84"] = bbox
        elif not base_meta.get("bbox_wgs84"):
            base_meta["bbox_wgs84"] = _bbox_wgs84_from_tuple(ctx.bbox)

        fc = _vector_feature_count_ogr(processed_path)
        if fc is not None:
            base_meta["feature_count"] = fc
        elif base_meta.get("feature_count") is None:
            base_meta["feature_count"] = 0

    out_meta_path = processed_meta_path or defn.processed_metadata_path(ctx)
    _write_json(out_meta_path, base_meta)


def _generate_raw_metadata(
    defn: DatasetDefinition,
    ctx: FetchContext,
    raw_path: Path,
    raw_meta_path: Path,
    fetch_command: List[str],
    metadata_overrides: Optional[Dict[str, object]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "dataset_name": defn.label,
        "category": defn.key,
        "project": ctx.project,
        "raw_path": str(raw_path),
        "fetch_tool": defn.fetch_tool,
        "fetch_command": " ".join(fetch_command),
        "fetch_date": _utc_iso(),
        "data_type": "Raster" if defn.dataset_type == "raster" else "Vector",
        "format": "GeoTIFF" if defn.dataset_type == "raster" else "GeoPackage",
        "file_size_bytes": raw_path.stat().st_size if raw_path.exists() else 0,
        "protocol_reference": DATASET_FETCH_PROTOCOL,
        "protocol_version": PROTOCOL_VERSION,
        "zeus_version": _get_zeus_version(),
        "tiles_downloaded": [],
        "notes": defn.description,
        "requested_bbox_wgs84": {
            "west": ctx.bbox[0],
            "south": ctx.bbox[1],
            "east": ctx.bbox[2],
            "north": ctx.bbox[3],
            "crs": "EPSG:4326",
        },
        "validation_status": "passed",
        "validation_date": _utc_iso(),
    }
    if defn.nodata is not None:
        payload["nodata_value"] = defn.nodata

    if metadata_overrides:
        overrides = copy.deepcopy(metadata_overrides)
        tiles = overrides.pop("tiles_downloaded", None)
        if tiles is not None:
            payload["tiles_downloaded"] = tiles
        for key, value in overrides.items():
            if value is not None:
                payload[key] = value

    info = _gdal_info(raw_path) if defn.dataset_type == "raster" else _ogr_info(raw_path)
    if info:
        payload["metadata"] = info
        if defn.dataset_type == "raster":
            epsg = _extract_epsg_from_info(info)
            if epsg:
                payload["raw_crs"] = epsg
        else:
            # For vectors, prefer inspecting the layer SRS directly (ogrinfo JSON may not contain EPSG IDs).
            v_epsg = _vector_epsg(raw_path)
            if v_epsg:
                payload["raw_crs"] = v_epsg

        if defn.dataset_type == "raster":
            extent = _extent_from_gdal_info(info)
            if extent:
                payload["extent"] = extent
            wgs84 = info.get("wgs84Extent")
            if isinstance(wgs84, dict):
                bbox = _bbox_from_wgs84_extent(wgs84)
                if bbox:
                    payload["bbox_wgs84"] = bbox
            stats = _extract_raster_statistics(info)
            if stats:
                payload["statistics"] = stats
        else:
            layers = info.get("layers") or []
            target_layer: Optional[Dict[str, Any]] = None
            for layer in layers:
                if layer.get("name") == "lines":
                    target_layer = layer
                    break
            if not target_layer and layers:
                target_layer = layers[0]

            if target_layer:
                extent = target_layer.get("extent")
                if extent:
                    payload["extent"] = {
                        "minx": extent.get("xmin"),
                        "miny": extent.get("ymin"),
                        "maxx": extent.get("xmax"),
                        "maxy": extent.get("ymax"),
                        "crs": target_layer.get("srs", "EPSG:4326"),
                    }
                payload["feature_count"] = target_layer.get("featureCount")
            if "feature_count" not in payload:
                payload["feature_count"] = 0

    if defn.dataset_type == "vector":
        if not payload.get("raw_crs"):
            v_epsg = _vector_epsg(raw_path)
            payload["raw_crs"] = v_epsg or "unknown"
        if not payload.get("extent"):
            extent_native = _vector_extent_dict(raw_path)
            payload["extent"] = extent_native or _bbox_dict_from_tuple(ctx.bbox)
        if not payload.get("bbox_wgs84"):
            bbox = _vector_bbox_wgs84(raw_path)
            payload["bbox_wgs84"] = bbox or _bbox_wgs84_from_tuple(ctx.bbox)
        if payload.get("feature_count") is None:
            fc = _vector_feature_count_ogr(raw_path)
            payload["feature_count"] = fc if fc is not None else 0

    checksum = _compute_file_sha256(raw_path)
    if checksum:
        payload["checksum_sha256"] = checksum

    _write_json(raw_meta_path, payload)

