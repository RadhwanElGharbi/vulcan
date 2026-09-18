"""Dataset-fetch package – data structures, dataset definitions, and Pydantic API models.

Contains:
- DatasetDefinition / FetchContext dataclasses
- DATASET_DEFINITIONS registry + metadata profiles
- Command-builder functions for each dataset category
- Pydantic request/response models for the REST API
"""

from __future__ import annotations

import copy
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from . import constants as _constants_mod
from .constants import (
    DATASET_FETCH_PROTOCOL,
    SOIL_DEFAULT_DEPTH,
    ZEUS_BIN,
    CommandBuilder,
)


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DatasetDefinition:
    key: str
    label: str
    dataset_type: Literal["raster", "vector"]
    raw_filename: str
    processed_basename: str
    processed_extension: str
    symlink_name: str
    fetch_tool: str
    command_builder: CommandBuilder
    resampling: str = "bilinear"
    nodata: Optional[float] = None
    required: bool = True
    description: str = ""
    # Backwards compatibility: allow resolving existing legacy artifacts without forcing re-fetch.
    # This is especially important for "Auto" datasets whose canonical naming is source-agnostic
    # (e.g., waterways/pipelines should not be prefixed with `osm_` when sourced from NHN/CER).
    legacy_raw_filenames: List[str] = field(default_factory=list)
    legacy_processed_basenames: List[str] = field(default_factory=list)

    def _base_dir(self, ctx: "FetchContext") -> Path:
        return ctx.project_path / "data" / ("rasters" if self.dataset_type == "raster" else "vectors")

    def get_raw_filename(self, override: Optional[str] = None) -> str:
        """Get raw filename, potentially modified based on override for DEM datasets."""
        override_text = (override or "").lower()
        if self.key == "dem" and override:
            # Dynamic filename based on DEM source
            if _is_3dep_override(override):
                return "dem_usgs_3dep_1m_raw.tif"
            elif "tinitaly" in override.lower():
                return "dem_tinitaly_10m_raw.tif"
            elif "eea" in override.lower() or "cop10" in override.lower():
                return "dem_copernicus_eea10_raw.tif"
            elif "srtm" in override.lower():
                return "dem_srtm_30m_raw.tif"
            elif _is_copernicus_override(override):
                return "dem_copernicus_30m_raw.tif"
        if self.key == "waterways":
            # Keep multiple sources in the same category separate:
            # - OSM -> osm_waterways_raw.gpkg
            # - Canada NHN (default) -> waterways_raw.gpkg
            if _override_requests_osm(override_text):
                return "osm_waterways_raw.gpkg"
        if self.key == "pipelines":
            # Keep multiple sources in the same category separate:
            # - OSM -> osm_pipelines_raw.gpkg
            # - Canada CER (default) -> pipelines_raw.gpkg
            if _override_requests_osm(override_text):
                return "osm_pipelines_raw.gpkg"
        return self.raw_filename

    def get_processed_basename(self, override: Optional[str] = None) -> str:
        """Get processed basename, potentially modified based on override for multi-source vector categories."""
        override_text = (override or "").lower()
        if self.key == "waterways":
            if _override_requests_osm(override_text):
                return "osm_waterways"
        if self.key == "pipelines":
            if _override_requests_osm(override_text):
                return "osm_pipelines"
        return self.processed_basename

    def raw_path(self, ctx: "FetchContext", override: Optional[str] = None) -> Path:
        return self._base_dir(ctx) / "raw" / self.get_raw_filename(override)

    def processed_path(self, ctx: "FetchContext", override: Optional[str] = None) -> Path:
        processed_base = self.get_processed_basename(override)
        processed_name = f"{processed_base}_epsg{ctx.target_epsg}_processed.{self.processed_extension}"
        return self._base_dir(ctx) / "processed" / processed_name

    def symlink_path(self, ctx: "FetchContext") -> Path:
        return self._base_dir(ctx) / self.symlink_name

    def raw_metadata_path(self, ctx: "FetchContext", override: Optional[str] = None) -> Path:
        raw = self.raw_path(ctx, override)
        return raw.with_suffix(raw.suffix + ".json")

    def processed_metadata_path(self, ctx: "FetchContext", override: Optional[str] = None) -> Path:
        proc = self.processed_path(ctx, override)
        return proc.with_suffix(proc.suffix + ".json")


@dataclass
class FetchContext:
    project: str
    project_path: Path
    target_epsg: int
    target_crs_name: Optional[str]
    iso3_list: List[str]
    bbox: Tuple[float, float, float, float]
    bbox_string: str
    aoi_file: Path
    cutline_path: Path
    log_dir: Path
    log_file: Path
    protocol_version: str = "1.0"

    @property
    def iso3(self) -> Optional[str]:
        """Primary country (first in the list). Backward-compatible accessor."""
        return self.iso3_list[0] if self.iso3_list else None

    def covers_country(self, code: str) -> bool:
        """Check if the AOI covers a specific country."""
        return code.upper() in (c.upper() for c in self.iso3_list)

    @property
    def is_single_country(self) -> bool:
        return len(self.iso3_list) == 1


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------


def _overpass_placeholder_command(*_: Any) -> List[str]:
    raise RuntimeError("Overpass datasets must be handled via _osm_overpass_fetch.")


def _dem_command(ctx: FetchContext, raw_path: Path, _: Path, override: Optional[str] = None) -> List[str]:
    """
    Build DEM fetch command with proper resolution and provider handling.
    
    Supports:
    - USGS 3DEP 1m LiDAR (USA) via opentopo provider with --res 1m
    - Copernicus EEA 10m (Europe) via copernicus_eea10_fetch tool
    - Copernicus GLO-30 (Global) via copernicus provider with --res 30m
    - TINITALY 10m (Italy) via tinitaly provider
    - SRTM 30m (Global) via srtm provider
    """
    provider = "auto"
    resolution = "30m"  # Default resolution
    normalized = (override or "").lower()
    
    # Detect resolution from override
    if "1m" in normalized or "1-meter" in normalized or "lidar" in normalized:
        resolution = "1m"
    elif "10m" in normalized or "10-meter" in normalized:
        resolution = "10m"
    elif "30m" in normalized or "30-meter" in normalized:
        resolution = "30m"
    
    # Detect provider from override
    if "3dep" in normalized or "usgs" in normalized:
        # USGS 3DEP is accessed via opentopo for 1m LiDAR
        provider = "opentopo"
        if "1m" in normalized or "lidar" in normalized:
            resolution = "1m"
        elif "10m" in normalized:
            resolution = "10m"
        else:
            resolution = "1m"  # Default to 1m for 3DEP as that's its strength
    elif "tinitaly" in normalized:
        provider = "tinitaly"
        resolution = "10m"  # TINITALY is 10m
    elif "copernicus" in normalized or "cop30" in normalized or "glo-30" in normalized:
        provider = "copernicus"
        resolution = "30m"
    elif "eea" in normalized or "cop10" in normalized or "glo-10" in normalized:
        # Copernicus EEA 10m for Europe - use dedicated tool
        return [
            str(ZEUS_BIN),
            "tools",
            "copernicus_eea10_fetch",
            "--bbox",
            ctx.bbox_string,
            "-o",
            str(raw_path),
            "--overwrite",
        ]
    elif "srtm" in normalized:
        provider = "srtm"
        resolution = "30m"
    elif "opentopo" in normalized:
        provider = "opentopo"
        # opentopo supports multiple resolutions
    elif ctx.is_single_country and ctx.covers_country("ITA"):
        provider = "tinitaly"
        resolution = "10m"
    elif ctx.is_single_country and ctx.covers_country("USA"):
        provider = "opentopo"
        resolution = "1m"
    
    return [
        str(ZEUS_BIN),
        "tools",
        "dem_fetch",
        "--bbox",
        ctx.bbox_string,
        "--res",
        resolution,
        "--provider",
        provider,
        "-o",
        str(raw_path),
        "--overwrite",
    ]


def _landcover_command(ctx: FetchContext, raw_path: Path, _: Path, override: Optional[str] = None) -> List[str]:
    normalized = (override or "").lower()
    year = "2021"
    year_match = re.search(r"20\d{2}", normalized)
    if year_match:
        year = year_match.group(0)
    return [
        str(ZEUS_BIN),
        "tools",
        "esa_worldcover_fetch",
        "--bbox",
        ctx.bbox_string,
        "--year",
        year,
        "-o",
        str(raw_path),
        "--overwrite",
    ]


def _landcover_fallback_command(ctx: FetchContext, raw_path: Path) -> List[str]:
    return [
        str(ZEUS_BIN),
        "tools",
        "google_dynamicworld_fetch",
        "--bbox",
        ctx.bbox_string,
        "--date",
        "latest",
        "-o",
        str(raw_path),
        "--overwrite",
    ]


def _soil_command(ctx: FetchContext, raw_path: Path, _: Path, override: Optional[str] = None) -> List[str]:
    property_name = "soc"
    normalized = (override or "").lower()
    if "clay" in normalized:
        property_name = "clay"
    elif "sand" in normalized:
        property_name = "sand"
    return [
        str(ZEUS_BIN),
        "tools",
        "soilgrids_fetch",
        "--bbox",
        ctx.bbox_string,
        "--properties",
        property_name,
        "--depth",
        SOIL_DEFAULT_DEPTH,
        "-o",
        str(raw_path),
        "--overwrite",
    ]


def _geohazard_command(ctx: FetchContext, raw_path: Path, _: Path, override: Optional[str] = None) -> List[str]:
    product = "pga"
    normalized = (override or "").lower()
    if "sa1" in normalized or "1.0" in normalized:
        product = "sa1.0"
    return [
        str(ZEUS_BIN),
        "tools",
        "seismic_hazard_fetch",
        "--bbox",
        ctx.bbox_string,
        "--product",
        product,
        "-o",
        str(raw_path),
        "--overwrite",
    ]


# ---------------------------------------------------------------------------
# Dataset definitions
# ---------------------------------------------------------------------------

DATASET_DEFINITIONS: Dict[str, DatasetDefinition] = {
    "dem": DatasetDefinition(
        key="dem",
        label="Digital Elevation Model (DEM)",
        dataset_type="raster",
        raw_filename="dem_global_30m_raw.tif",
        processed_basename="dem",
        processed_extension="tif",
        symlink_name="dem.tif",
        fetch_tool="dem_fetch",
        command_builder=_dem_command,
        resampling="bilinear",
        nodata=-32768.0,
        description="Core elevation surface used for slope, hydraulics and terrain costs.",
    ),
    "landcover": DatasetDefinition(
        key="landcover",
        label="Landcover (ESA WorldCover 10m)",
        dataset_type="raster",
        raw_filename="landcover_esa_worldcover_raw.tif",
        processed_basename="landcover",
        processed_extension="tif",
        symlink_name="landcover.tif",
        fetch_tool="esa_worldcover_fetch",
        command_builder=_landcover_command,
        resampling="near",
        nodata=0.0,
        description="10 m categorical landcover classes (agriculture, forest, urban, etc.).",
    ),
    "soil": DatasetDefinition(
        key="soil",
        label="Soil (SoilGrids v2.0)",
        dataset_type="raster",
        raw_filename="soil_soilgrids_250m_raw.tif",
        processed_basename="soil",
        processed_extension="tif",
        symlink_name="soil.tif",
        fetch_tool="soilgrids_fetch",
        command_builder=_soil_command,
        resampling="bilinear",
        nodata=-9999.0,
        description="ISRIC SoilGrids mean SOC at 0-5 cm depth, native 250 m grid.",
    ),
    "geohazard": DatasetDefinition(
        key="geohazard",
        label="Geohazards (GEM/USGS PGA)",
        dataset_type="raster",
        raw_filename="geohazards_gem_seismic_raw.tif",
        processed_basename="geohazards",
        processed_extension="tif",
        symlink_name="geohazards.tif",
        fetch_tool="seismic_hazard_fetch",
        command_builder=_geohazard_command,
        resampling="near",
        nodata=-9999.0,
        description="Peak ground acceleration (PGA) map for seismic risk screening.",
    ),
    "roads": DatasetDefinition(
        key="roads",
        label="Roads (OSM)",
        dataset_type="vector",
        raw_filename="osm_roads_raw.gpkg",
        processed_basename="osm_roads",
        processed_extension="gpkg",
        symlink_name="roads.gpkg",
        fetch_tool="osm_roads_fetch",
        command_builder=_overpass_placeholder_command,
        description="OpenStreetMap road network (highways + local roads).",
    ),
    "railways": DatasetDefinition(
        key="railways",
        label="Railways (OSM)",
        dataset_type="vector",
        raw_filename="osm_railways_raw.gpkg",
        processed_basename="osm_railways",
        processed_extension="gpkg",
        symlink_name="railways.gpkg",
        fetch_tool="osm_railways_fetch",
        command_builder=_overpass_placeholder_command,
        description="OpenStreetMap rail corridors for crossing avoidance.",
    ),
    "powerlines": DatasetDefinition(
        key="powerlines",
        label="Power Lines (OSM)",
        dataset_type="vector",
        raw_filename="osm_power_lines_raw.gpkg",
        processed_basename="osm_power_lines",
        processed_extension="gpkg",
        symlink_name="power_lines.gpkg",
        fetch_tool="osm_power_fetch",
        command_builder=_overpass_placeholder_command,
        description="Transmission + distribution assets for clearance buffers.",
    ),
    "waterways": DatasetDefinition(
        key="waterways",
        label="Waterways (Auto)",
        dataset_type="vector",
        # Canonical naming is source-agnostic (NHN for Canada by default; OSM fallback on request)
        raw_filename="waterways_raw.gpkg",
        processed_basename="waterways",
        processed_extension="gpkg",
        symlink_name="waterways.gpkg",
        fetch_tool="osm_waterways_fetch",
        command_builder=_overpass_placeholder_command,
        description="Rivers + streams used for hydraulic constraints (NHN for Canada where available; otherwise OSM).",
        legacy_raw_filenames=["osm_waterways_raw.gpkg"],
        legacy_processed_basenames=["osm_waterways"],
    ),
    "pipelines": DatasetDefinition(
        key="pipelines",
        label="Pipelines (Auto)",
        dataset_type="vector",
        # Canonical naming is source-agnostic (CER for Canada by default; OSM fallback on request)
        raw_filename="pipelines_raw.gpkg",
        processed_basename="pipelines",
        processed_extension="gpkg",
        symlink_name="pipelines.gpkg",
        fetch_tool="osm_pipelines_overpass",
        command_builder=_overpass_placeholder_command,
        description="Existing pipelines for ROW alignment + crossings (CER for Canada by default; OSM fallback on request).",
        legacy_raw_filenames=["osm_pipelines_raw.gpkg"],
    ),
    "protected_areas": DatasetDefinition(
        key="protected_areas",
        label="Protected Areas (CPCAD)",
        dataset_type="vector",
        raw_filename="protected_areas_cpcad_raw.gpkg",
        processed_basename="protected_areas",
        processed_extension="gpkg",
        symlink_name="protected_areas.gpkg",
        fetch_tool="cpcad_fetch",
        command_builder=lambda *_args, **_kwargs: [],  # handled via native pipeline
        required=False,
        description="Canadian Protected and Conserved Areas Database (CPCAD) polygons for environmental constraints.",
    ),
    "indigenous_lands": DatasetDefinition(
        key="indigenous_lands",
        label="Indigenous Lands (Canada — CLSS)",
        dataset_type="vector",
        raw_filename="indigenous_lands_raw.geojson",
        processed_basename="indigenous_lands",
        processed_extension="gpkg",
        symlink_name="indigenous_lands.gpkg",
        fetch_tool="db_indigenous_lands_fetch",
        command_builder=lambda *_args, **_kwargs: [],  # handled via native DB cache pipeline
        required=False,
        description="Canada Aboriginal/Indigenous lands legislative boundaries (NRCan CLSS), cached locally in /opt/agrs/DBs.",
    ),
}

REQUIRED_DATASETS = list(DATASET_DEFINITIONS.keys())

# ---------------------------------------------------------------------------
# Dataset metadata profiles
# ---------------------------------------------------------------------------

DATASET_METADATA_PROFILES: Dict[str, Dict[str, Dict[str, object]]] = {
    "dem": {
        "auto": {
            "dataset_name": "Global DEM 30m (Auto)",
            "source": "Copernicus DEM / NASA SRTM (auto-selected)",
            "provider": "ESA & NASA",
            "provider_url": "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
            "coverage_date": "2021",
            "documentation_url": "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
            "license": "Copernicus Data License / NASA SRTM Terms",
            "attribution": "Contains Copernicus DEM and NASA SRTM data.",
            "resolution_m": 30,
        },
        "3dep": {
            "dataset_name": "USGS 3DEP LiDAR DEM 1m",
            "source": "USGS 3D Elevation Program (3DEP) LiDAR",
            "provider": "U.S. Geological Survey",
            "provider_url": "https://www.usgs.gov/3d-elevation-program",
            "coverage_date": "Current (continuously updated)",
            "documentation_url": "https://www.usgs.gov/3d-elevation-program",
            "license": "Public Domain (US Government Work)",
            "attribution": "U.S. Geological Survey 3D Elevation Program (3DEP).",
            "resolution_m": 1,
            "notes": "Highest quality national elevation data for the US. LiDAR-derived bare-earth DEM with vertical accuracy typically better than 10cm RMSE.",
        },
        "eea10": {
            "dataset_name": "Copernicus DEM EEA-10",
            "source": "Copernicus DEM (EEA-10)",
            "provider": "European Space Agency (Copernicus)",
            "provider_url": "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
            "coverage_date": "2021",
            "documentation_url": "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
            "license": "Copernicus Data License",
            "attribution": "Contains modified Copernicus Sentinel data (2021) processed by ESA.",
            "resolution_m": 10,
            "notes": "10m resolution DEM available for European Economic Area.",
        },
        "copernicus": {
            "dataset_name": "Copernicus DEM GLO-30",
            "source": "Copernicus DEM (GLO-30)",
            "provider": "European Space Agency (Copernicus)",
            "provider_url": "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
            "coverage_date": "2021",
            "documentation_url": "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
            "license": "Copernicus Data License",
            "attribution": "Contains modified Copernicus Sentinel data (2021) processed by ESA.",
            "resolution_m": 30,
        },
        "tinitaly": {
            "dataset_name": "TINITALY DEM 10m",
            "source": "TINITALY/01 square km Digital Elevation Model",
            "provider": "INGV - Istituto Nazionale di Geofisica e Vulcanologia",
            "provider_url": "https://tinitaly.pi.ingv.it/",
            "coverage_date": "2011",
            "documentation_url": "https://tinitaly.pi.ingv.it/Download_Area1.html",
            "license": "CC BY 4.0",
            "attribution": "TINITALY DEM © INGV.",
            "resolution_m": 10,
        },
        "srtm": {
            "dataset_name": "NASA SRTM GL1 30m",
            "source": "Shuttle Radar Topography Mission (SRTM) GL1",
            "provider": "NASA Jet Propulsion Laboratory",
            "provider_url": "https://www2.jpl.nasa.gov/srtm/",
            "coverage_date": "2000",
            "documentation_url": "https://www2.jpl.nasa.gov/srtm/",
            "license": "Public Domain",
            "attribution": "Contains NASA SRTM data.",
            "resolution_m": 30,
        },
    },
    "landcover": {
        "default": {
            "dataset_name": "ESA WorldCover 10m",
            "source": "ESA WorldCover",
            "provider": "European Space Agency / VITO",
            "provider_url": "https://worldcover2021.esa.int/",
            "coverage_date": "2021",
            "documentation_url": "https://worldcover2021.esa.int/",
            "license": "CC BY 4.0",
            "attribution": "© ESA WorldCover, produced by VITO.",
            "resolution_m": 10,
        }
    },
    "soil": {
        "default": {
            "dataset_name": "SoilGrids v2.0 (SOC 0-30cm)",
            "source": "ISRIC SoilGrids v2.0",
            "provider": "ISRIC - World Soil Information",
            "provider_url": "https://soilgrids.org/",
            "coverage_date": "2020",
            "documentation_url": "https://www.isric.org/explore/soilgrids",
            "license": "CC BY 4.0",
            "attribution": "Contains ISRIC SoilGrids data.",
            "resolution_m": 250,
        }
    },
    "geohazard": {
        "default": {
            "dataset_name": "GEM Global Seismic Hazard (PGA)",
            "source": "GEM / USGS Global Hazard Model",
            "provider": "Global Earthquake Model (GEM) Foundation",
            "provider_url": "https://hazard.openquake.org/",
            "coverage_date": "2018",
            "documentation_url": "https://hazard.openquake.org/",
            "license": "CC BY-NC-SA 4.0",
            "attribution": "Contains data © GEM Foundation.",
            "resolution_m": 1000,
        }
    },
    "roads": {
        "default": {
            "dataset_name": "OpenStreetMap Roads Extract",
            "source": "OpenStreetMap (highway)",
            "provider": "OpenStreetMap Contributors",
            "provider_url": "https://www.openstreetmap.org",
            "coverage_date": "Current (live OSM)",
            "documentation_url": "https://wiki.openstreetmap.org/wiki/Overpass_API",
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors.",
            "resolution_m": None,
        }
    },
    "railways": {
        "default": {
            "dataset_name": "OpenStreetMap Railways Extract",
            "source": "OpenStreetMap (railway)",
            "provider": "OpenStreetMap Contributors",
            "provider_url": "https://www.openstreetmap.org",
            "coverage_date": "Current (live OSM)",
            "documentation_url": "https://wiki.openstreetmap.org/wiki/Overpass_API",
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors.",
            "resolution_m": None,
        }
    },
    "powerlines": {
        "default": {
            "dataset_name": "OpenStreetMap Power Network Extract",
            "source": "OpenStreetMap (power)",
            "provider": "OpenStreetMap Contributors",
            "provider_url": "https://www.openstreetmap.org",
            "coverage_date": "Current (live OSM)",
            "documentation_url": "https://wiki.openstreetmap.org/wiki/Overpass_API",
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors.",
            "resolution_m": None,
        }
    },
    "waterways": {
        "default": {
            "dataset_name": "OpenStreetMap Waterways Extract",
            "source": "OpenStreetMap (waterway)",
            "provider": "OpenStreetMap Contributors",
            "provider_url": "https://www.openstreetmap.org",
            "coverage_date": "Current (live OSM)",
            "documentation_url": "https://wiki.openstreetmap.org/wiki/Overpass_API",
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors.",
            "resolution_m": None,
        }
    },
    "pipelines": {
        "default": {
            "dataset_name": "OpenStreetMap Pipelines Extract",
            "source": "OpenStreetMap (pipeline)",
            "provider": "OpenStreetMap Contributors",
            "provider_url": "https://www.openstreetmap.org",
            "coverage_date": "Current (live OSM)",
            "documentation_url": "https://wiki.openstreetmap.org/wiki/Overpass_API",
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors.",
            "resolution_m": None,
        }
    },
}

# ---------------------------------------------------------------------------
# Overpass API config
# ---------------------------------------------------------------------------

OSM_OVERPASS_FILTERS: Dict[str, List[str]] = {
    "roads": ['way["highway"]', 'relation["highway"]'],
    "railways": ['way["railway"]', 'relation["railway"]'],
    "powerlines": ['way["power"~"line|minor_line"]', 'relation["power"~"line|minor_line"]'],
    "waterways": ['way["waterway"]', 'relation["waterway"]'],
    "pipelines": [
        'way["man_made"="pipeline"]',
        'way["pipeline"]',
        'relation["man_made"="pipeline"]',
        'relation["pipeline"]',
    ],
}

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


# ---------------------------------------------------------------------------
# Metadata / version helpers
# ---------------------------------------------------------------------------


def _get_zeus_version() -> str:
    if _constants_mod._ZEUS_VERSION_CACHE is not None:
        return _constants_mod._ZEUS_VERSION_CACHE
    if not ZEUS_BIN.exists():
        _constants_mod._ZEUS_VERSION_CACHE = "unavailable"
        return _constants_mod._ZEUS_VERSION_CACHE
    try:
        result = subprocess.run([str(ZEUS_BIN), "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            _constants_mod._ZEUS_VERSION_CACHE = lines[0] if lines else "unknown"
        else:
            _constants_mod._ZEUS_VERSION_CACHE = "unknown"
    except Exception:  # noqa: BLE001
        _constants_mod._ZEUS_VERSION_CACHE = "unknown"
    return _constants_mod._ZEUS_VERSION_CACHE


def _metadata_profile(category: str, profile_key: str) -> Dict[str, object]:
    profiles = DATASET_METADATA_PROFILES.get(category, {})
    base = profiles.get(profile_key) or profiles.get("default") or {}
    return copy.deepcopy(base)


def _normalize_override(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _override_requests_osm(text: str) -> bool:
    """
    Determine whether a user override is explicitly requesting an OpenStreetMap-derived dataset.

    NOTE: The UI may pass values like "OpenStreetMap Waterways Extract", not just "OSM",
    so we match multiple common substrings.
    """
    normalized = (text or "").lower()
    return (
        "osm" in normalized
        or "openstreetmap" in normalized
        or "open street map" in normalized
        or "overpass" in normalized
    )


def _is_copernicus_override(text: str) -> bool:
    normalized = (text or "").lower()
    return "copernicus" in normalized or "cop30" in normalized or "glo-30" in normalized


def _is_3dep_override(text: str) -> bool:
    """Check if the override requests USGS 3DEP / 1m LiDAR DEM.

    Enhanced to catch more variations including:
    - Direct mentions: 3dep, usgs, lidar, opentopo
    - Resolution mentions: 1m, 1-meter, 1 meter, one meter, 1-m
    - Quality mentions: high resolution, high-res, highest, best
    - Combined: usgs 1m, 3dep lidar, etc.
    """
    normalized = (text or "").lower().strip()
    if not normalized:
        return False

    # Direct source keywords
    direct_keywords = ["3dep", "usgs dem", "usgs 3d", "lidar", "opentopo", "national map", "tnm"]
    if any(kw in normalized for kw in direct_keywords):
        return True

    # Resolution-based detection (1 meter DEM)
    resolution_patterns = [
        "1m", "1-m", "1 m", "1meter", "1-meter", "1 meter",
        "one meter", "onemeter", "1 metre", "1-metre",
        "high res", "high-res", "highres", "highest res",
        "best resolution", "best quality", "finest"
    ]
    if any(pattern in normalized for pattern in resolution_patterns):
        return True

    # Check for "1" followed by "m" or "meter" with possible spaces/hyphens
    import re as _re
    if _re.search(r'\b1\s*[-]?\s*m(?:eter|etre)?\b', normalized):
        return True

    return False


def _resolve_profile_key(defn: DatasetDefinition, ctx: FetchContext, override_text: str) -> str:
    if defn.key == "dem":
        # Check for 3DEP/1m first (highest resolution)
        if _is_3dep_override(override_text):
            return "3dep"
        if "tinitaly" in override_text:
            return "tinitaly"
        if "eea" in override_text or "cop10" in override_text or "glo-10" in override_text:
            return "eea10"
        if _is_copernicus_override(override_text):
            return "copernicus"
        if "srtm" in override_text:
            return "srtm"
        if ctx.is_single_country and ctx.covers_country("ITA"):
            return "tinitaly"
        if ctx.is_single_country and ctx.covers_country("USA"):
            return "3dep"
        return "auto"
    return "default"


def _extract_year_from_text(text: str) -> Optional[str]:
    match = re.search(r"(20\d{2})", text or "")
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Pydantic API models
# ---------------------------------------------------------------------------


class DatasetCategoryStatus(BaseModel):
    category: str
    label: str
    dataset_type: Literal["raster", "vector"]
    required: bool
    present: bool
    raw_path: Optional[str]
    processed_path: Optional[str]
    metadata_path: Optional[str]
    last_modified: Optional[str]
    description: Optional[str]


class DatasetStatusResponse(BaseModel):
    project: str
    target_epsg: int
    minimum_requirements_met: bool
    categories: List[DatasetCategoryStatus]
    protocol_reference: str = DATASET_FETCH_PROTOCOL


class DatasetFetchRequest(BaseModel):
    categories: List[str] = Field(..., description="Dataset category identifiers to fetch.")
    force: bool = Field(False, description="If true, refetch even if processed dataset already exists.")
    overrides: Optional[Dict[str, str]] = Field(default=None, description="Optional preferred dataset sources per category.")


class DatasetFetchJobResponse(BaseModel):
    job_id: str


class StageState(BaseModel):
    """State of a single processing stage."""
    status: str
    message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class CategoryState(BaseModel):
    """State of a dataset category with its stages."""
    status: str
    message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    stages: Dict[str, StageState]


class DatasetJobResponse(BaseModel):
    id: str
    project: str
    status: Literal["pending", "running", "succeeded", "failed", "partial"]
    progress: float
    current_category: Optional[str]
    started_at: Optional[datetime]
    updated_at: datetime
    completed_at: Optional[datetime]
    categories: Dict[str, Any]  # Raw dict to avoid Pydantic validation issues with nested dicts
    logs: List[str]
    force: bool
    error: Optional[str]
    overrides: Optional[Dict[str, str]] = None


class ActiveDatasetJobInfo(BaseModel):
    """Minimal active-job state keyed by project (used by frontend sidebar poll)."""

    job_id: str
    status: Literal["pending", "running", "succeeded", "failed", "partial"]
    progress: float
    current_category: Optional[str]


class ActiveDatasetJobsResponse(BaseModel):
    active_jobs: Dict[str, ActiveDatasetJobInfo]


def _build_metadata_context(
    defn: DatasetDefinition,
    ctx: FetchContext,
    override: Optional[str],
    fetch_info: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    normalized = _normalize_override(override)
    profile_key = _resolve_profile_key(defn, ctx, normalized)
    metadata = _metadata_profile(defn.key, profile_key)
    metadata.setdefault("notes", defn.description)
    if "resolution_m" not in metadata:
        metadata["resolution_m"] = 30 if defn.dataset_type == "raster" else None
    metadata["category"] = defn.key
    metadata["project"] = ctx.project
    metadata["selected_override"] = override if override else None
    metadata.setdefault("tiles_downloaded", [])

    if fetch_info:
        if "tiles_downloaded" in fetch_info and fetch_info["tiles_downloaded"]:
            metadata["tiles_downloaded"] = fetch_info["tiles_downloaded"]  # type: ignore[assignment]
        for key, value in fetch_info.items():
            if value is None:
                continue
            if key == "tiles_downloaded":
                continue
            metadata[key] = value  # type: ignore[assignment]

    if defn.key == "landcover":
        dataset_variant = fetch_info.get("landcover_dataset") if fetch_info else None
        if dataset_variant == "google_dynamic_world":
            metadata["dataset_name"] = "Google Dynamic World 10m"
            metadata["source"] = "Google Dynamic World"
            metadata["provider"] = "Google / World Resources Institute"
            metadata["provider_url"] = "https://www.dynamicworld.app/"
            metadata["documentation_url"] = "https://www.dynamicworld.app/explore"
            metadata["license"] = "CC BY 4.0"
            metadata["attribution"] = "© Google, World Resources Institute"
            if fetch_info and fetch_info.get("coverage_date"):
                metadata["coverage_date"] = fetch_info["coverage_date"]
        else:
            year = _extract_year_from_text(normalized) or metadata.get("coverage_date") or "2021"
            metadata["coverage_date"] = str(year)
            metadata["dataset_name"] = f"ESA WorldCover {year}"
            metadata["source"] = f"ESA WorldCover {year}"
    elif defn.key == "soil":
        property_label = "SOC"
        if "clay" in normalized:
            property_label = "Clay"
        elif "sand" in normalized:
            property_label = "Sand"
        metadata["dataset_name"] = f"SoilGrids v2.0 ({property_label} {SOIL_DEFAULT_DEPTH})"
        metadata["coverage_depth"] = SOIL_DEFAULT_DEPTH
        metadata["notes"] = f"{defn.description} Property: {property_label} at {SOIL_DEFAULT_DEPTH}."
    elif defn.key == "geohazard":
        product = "PGA"
        if "sa1" in normalized or "1.0" in normalized:
            product = "SA(1.0s)"
        metadata["dataset_name"] = f"GEM Global Seismic Hazard ({product})"
        metadata["source"] = f"GEM hazard surface {product}"

    return metadata
