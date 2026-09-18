"""Dataset-fetch package – configuration constants and environment bindings.

Every tunable knob, URL, path, regex pattern and type alias lives here so that
the rest of the package can ``from .constants import …`` without pulling in
heavy framework dependencies (FastAPI, SQLAlchemy, etc.).
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Callable, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DOCS_ROOT = _REPO_ROOT / "docs"

def _first_existing(*paths: Path) -> str:
    for p in paths:
        if p.exists():
            return str(p)
    return str(paths[0]) if paths else ""

DATASET_FETCH_PROTOCOL = _first_existing(
    _DOCS_ROOT / "datasets" / "DATASET_FETCHING_PROTOCOLS.md",
    _DOCS_ROOT / "Project Instructions" / "DATASET_FETCHING_PROTOCOLS.md",
)

# ---------------------------------------------------------------------------
# Tool binaries
# ---------------------------------------------------------------------------

ZEUS_BIN = Path(os.getenv("AGRS_ZEUS_BIN", "/opt/agrs/build/zeus"))
GDALWARP_BIN = os.getenv("GDALWARP_BIN", "gdalwarp")
OGR2OGR_BIN = os.getenv("OGR2OGR_BIN", "ogr2ogr")
GDALINFO_BIN = os.getenv("GDALINFO_BIN", "gdalinfo")
OGRINFO_BIN = os.getenv("OGRINFO_BIN", "ogrinfo")

# ---------------------------------------------------------------------------
# Remote-service URLs
# ---------------------------------------------------------------------------

COPERNICUS_BASE_URL = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"
COPERNICUS_PRODUCT = "Copernicus_DSM_COG_10"

CER_PIPELINES_LAYER_URL = (
    "https://services5.arcgis.com/vNzamREXvX2WcX6d/arcgis/rest/services/"
    "CER_Pipeline_Systems_WGS84_view/FeatureServer/3"
)
CPCAD_LAYER_URL = "https://maps-cartes.ec.gc.ca/arcgis/rest/services/CWS_SCF/CPCAD/MapServer/0"
NHN_INDEX_ZIP_URL = "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/index/NHN_INDEX_WORKUNIT_LIMIT_2.zip"
NHN_TILE_BASE_URL = "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/shp_en"

# Canada Lands Survey System (CLSS) administrative boundaries (Aboriginal/Indigenous lands)
CLSS_ABORIGINAL_LANDS_LAYER_URL = (
    "https://proxyinternet.nrcan.gc.ca/arcgis/rest/services/CLSS-SATC/CLSS_Administrative_Boundaries/MapServer/0"
)

# ---------------------------------------------------------------------------
# Global raw-dataset cache (raw-only; no project-specific processing here)
# ---------------------------------------------------------------------------

DBS_ROOT = Path(os.getenv("AGRS_DBS_ROOT", "/opt/agrs/DBs"))
DB_INDEX_CSV = DBS_ROOT / "db_index.csv"
DB_MATERIALIZE_MODE = os.getenv("AGRS_DBS_MATERIALIZE_MODE", "symlink").strip().lower()
DB_LOCK = threading.Lock()

# Initial DB entry: Canada Indigenous/Aboriginal lands (CLSS legislative boundaries)
CAN_INDIGENOUS_LANDS_DB_ID = "can_indigenous_lands_clss_aboriginal_lands"
CAN_INDIGENOUS_LANDS_DB_RAW_FILENAME = "aboriginal_lands_of_canada_legislative_boundaries.geojson"

# ---------------------------------------------------------------------------
# Agent / LLM config
# ---------------------------------------------------------------------------

_AGENT_FLAG = "0"
AGENT_ENABLED = False  # Legacy compatibility only; research acquisition never imports the agent.
AGENT_MODEL = os.getenv("ZEUS_AGENT_MODEL", "claude-3.5-sonnet")
try:
    AGENT_MAX_STEPS = max(1, int(os.getenv("ZEUS_AGENT_MAX_STEPS", "500")))
except ValueError:
    AGENT_MAX_STEPS = 500
try:
    AGENT_MAX_RETRIES = max(1, int(os.getenv("ZEUS_AGENT_MAX_RETRIES", "8")))
except ValueError:
    AGENT_MAX_RETRIES = 8

# ---------------------------------------------------------------------------
# Protocol config
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "1.0"
SOIL_DEFAULT_DEPTH = "0-5cm"
PROTOCOL_PATH = Path(DATASET_FETCH_PROTOCOL)

# ---------------------------------------------------------------------------
# Caches & patterns
# ---------------------------------------------------------------------------

_ZEUS_VERSION_CACHE: Optional[str] = None
_PROTOCOL_TEXT_CACHE: Optional[str] = None

_EXTENT_RE = re.compile(
    r"Extent:\s*\(([-0-9\.]+),\s*([-0-9\.]+)\)\s*-\s*\(([-0-9\.]+),\s*([-0-9\.]+)\)"
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

CommandBuilder = Callable[["FetchContext", Path, Path, Optional[str]], List[str]]
