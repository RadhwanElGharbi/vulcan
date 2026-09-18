from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    AGENT_ENABLED,
    AGENT_MAX_RETRIES,
    AGENT_MAX_STEPS,
    AGENT_MODEL,
    COPERNICUS_BASE_URL,
    ZEUS_BIN,
)
from .job_state import DatasetJobState, _log_to_job, _run_command_capture
from .models import DatasetDefinition, FetchContext, _is_3dep_override
from .utils import _extract_json_payload, _load_project_metadata_blob, _load_protocol_text


def _agent_api_key() -> Optional[str]:
    if not AGENT_ENABLED:
        return None
    api_key = os.getenv("CURSOR_API_KEY")
    return api_key or None


def _build_agent_prompt(defn: DatasetDefinition, ctx: FetchContext, override: Optional[str]) -> str:
    metadata_blob = _load_project_metadata_blob(ctx)
    protocol_blob = _load_protocol_text()
    data_dir = ctx.project_path / "data"
    raw_path = defn.raw_path(ctx, override)
    processed_path = defn.processed_path(ctx, override)
    override_text = override or "Use catalog default"
    schema_hint = json.dumps(
        {
            "steps": [
                {"description": "Describe the action", "command": "bash command to execute from project root"}
            ],
            "post_checks": [
                {"description": "Validation step after processing", "command": "bash command ensuring compliance"}
            ],
            "notes": "Optional short summary of key decisions",
        },
        indent=2,
    )
    return dedent(
        f"""
        You are ZEUS AI, responsible for fully executing AGRS dataset ingestion workflows.
        Every command you emit will run via `/bin/bash -lc "<command>"` with working directory `{ctx.project_path}`.
        Obey these rules without exception:
          • Follow the Dataset Fetching Protocols in their entirety (text provided below).
          • Inspect existing data before downloading anything to avoid duplicates.
          • Fetch only authoritative, non-placeholder data that fully covers the AOI (bbox {ctx.bbox_string}).
          • Write raw outputs to `{raw_path}` inside `{data_dir}` without altering original values.
          • Reproject/clamp outputs to EPSG:{ctx.target_epsg} ({ctx.target_crs_name}) using cutline `{ctx.cutline_path}` and
            store the processed artifact at `{processed_path}` with minimal padding outside the AOI.
          • Use `gdalwarp`, `ogr2ogr`, `gdalbuildvrt`, `zeus tools {defn.fetch_tool}`, curl/wget, and python/gdal utilities as needed.
          • Never use sudo or destructive commands outside the project directory tree.
          • Keep raw + processed files under `data/rasters` or `data/vectors` per dataset type `{defn.dataset_type}`.
          • Ensure outputs remain within AOI extent (no extra padding beyond protocol buffer allowances).

        Project context:
          • Project: {ctx.project} (Countries: {', '.join(ctx.iso3_list) or 'Unknown'})
          • Dataset category: {defn.key} – {defn.label} ({defn.dataset_type})
          • Preferred/override source: {override_text}
          • ZEUS CLI binary: {ZEUS_BIN}
          • Project metadata path: {ctx.project_path / "project_metadata.json"}
          • Data root: {data_dir}

        Project metadata JSON (read and follow CRS + AOI guidance):
        {metadata_blob}

        Dataset Fetching Protocol (read fully before planning steps):
        {protocol_blob}

        Respond STRICTLY with JSON following this template:
        {schema_hint}
        """
    ).strip()


class AgentConversation:
    """
    Maintains full conversational context for ZEUS AI throughout the entire
    dataset fetching pipeline. This allows ZEUS AI to learn from successes,
    failures, and outputs across all operations.
    """
    
    def __init__(self, ctx: "FetchContext", job: "DatasetJobState"):
        self.ctx = ctx
        self.job = job
        self.messages: List[Dict[str, str]] = []
        self.system_prompt = self._build_system_prompt()
        self.completed_datasets: List[str] = []
        self.failed_datasets: List[str] = []
        self.total_commands_executed = 0
        self.api_key = _agent_api_key()
        
        if not self.api_key:
            raise RuntimeError("ZEUS AI unavailable (missing CURSOR_API_KEY or agent disabled).")
        
        try:
            from openai import OpenAI  # type: ignore
            self.client = OpenAI(
                api_key=self.api_key,
                base_url="https://api.cursor.com/v1",
            )
        except ImportError as exc:
            raise RuntimeError("ZEUS AI client library (openai) not installed on server.") from exc
    
    def _build_system_prompt(self) -> str:
        scripts_dir = self.ctx.project_path / "scripts"
        return dedent(
            f"""
            You are ZEUS AI, a fully autonomous geospatial data engineer with COMPLETE AGENCY to fetch 
            datasets for pipeline routing projects. You can execute ANY bash command and must use ALL 
            available means to acquire the requested data.
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            CORE MANDATE: FIND A WAY - ANY WAY - TO GET THE DATA
            ═══════════════════════════════════════════════════════════════════════════════════════════
            
            You are NOT limited to pre-built tools. You have full authority to:
            1. Write Python scripts to call ANY public API
            2. Scrape data from official government/agency websites
            3. Use STAC (SpatioTemporal Asset Catalog) APIs to discover datasets
            4. Query WMS/WFS/WCS services directly with GDAL
            5. Download from S3/Azure/GCS buckets if publicly accessible
            6. Create custom fetch tools and save them to {scripts_dir}/
            7. Chain multiple data sources together
            8. Parse HTML pages to find download links
            
            AUTONOMY LEVELS (try in order):
            Level 1: Use ZEUS CLI tools if they exist and work
            Level 2: Use direct API calls (curl, wget) to known endpoints
            Level 3: Query STAC catalogs to discover data (see STAC DISCOVERY below)
            Level 4: Write Python scripts to interact with complex APIs
            Level 5: Scrape official data portals to find download URLs
            Level 6: Use GDAL virtual file systems (/vsicurl/, /vsis3/) for cloud data
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            AVAILABLE TOOLS & CAPABILITIES
            ═══════════════════════════════════════════════════════════════════════════════════════════
            
            COMMAND EXECUTION:
            - All commands run via: /bin/bash -lc "<command>"
            - Working directory: {self.ctx.project_path}
            - You can write multi-line scripts using heredocs or echo to files
            
            BUILT-IN TOOLS:
            - ZEUS CLI: {ZEUS_BIN} (check available tools with: {ZEUS_BIN} tools --help)
            - GDAL/OGR: gdalwarp, gdal_translate, gdalinfo, gdalbuildvrt, ogr2ogr, ogrinfo, gdal_contour
            - Network: curl, wget, python3 (with requests, httpx if available)
            - Processing: jq (JSON), xmllint (XML), sed, awk, grep
            
            PYTHON SCRIPTING:
            You can write and execute Python scripts inline:
            ```
            python3 << 'EOF'
            import requests
            # Your code here
            EOF
            ```
            Or save persistent scripts to {scripts_dir}/ for reuse.
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            STAC DISCOVERY - USE THIS TO FIND DATA
            ═══════════════════════════════════════════════════════════════════════════════════════════
            
            STAC (SpatioTemporal Asset Catalog) is a standard for discovering geospatial data.
            Query these STAC endpoints to find datasets:
            
            - Microsoft Planetary Computer: https://planetarycomputer.microsoft.com/api/stac/v1
              Collections: cop-dem-glo-30, cop-dem-glo-90, nasadem, alos-dem, io-lulc, esa-worldcover
              
            - Earth Search (AWS): https://earth-search.aws.element84.com/v1
              Collections: cop-dem-glo-30, sentinel-2-l2a, landsat-c2-l2
              
            - USGS STAC: https://landsatlook.usgs.gov/stac-server
              
            Example STAC query:
            ```
            curl -s "https://earth-search.aws.element84.com/v1/search" \\
              -H "Content-Type: application/json" \\
              -d '{{"collections":["cop-dem-glo-30"],"bbox":[{self.ctx.bbox[0]},{self.ctx.bbox[1]},{self.ctx.bbox[2]},{self.ctx.bbox[3]}],"limit":10}}' | jq '.features[].assets'
            ```
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            DATA SOURCE CATALOG (by category)
            ═══════════════════════════════════════════════════════════════════════════════════════════
            
            DEM (Digital Elevation Models):
            ─────────────────────────────────
            • USGS 3DEP 1m LiDAR (USA - highest resolution):
              - OpenTopography API: https://portal.opentopography.org/API/globaldem?demtype=SRTMGL1&south=LAT&north=LAT&west=LON&east=LON&outputFormat=GTiff
              - TNM API: https://tnmaccess.nationalmap.gov/api/v1/products?datasets=Digital%20Elevation%20Model%20(DEM)%201%20meter&bbox=WEST,SOUTH,EAST,NORTH
              - Direct tiles: https://prd-tnm.s3.amazonaws.com/index.html?prefix=StagedProducts/Elevation/1m/
            • Copernicus DEM GLO-30 (Global 30m):
              - S3: s3://copernicus-dem-30m/ (via /vsis3/ or direct HTTPS)
              - Tiles: {COPERNICUS_BASE_URL}/Copernicus_DSM_COG_10_NXX_00_EXXX_00_DEM/Copernicus_DSM_COG_10_NXX_00_EXXX_00_DEM.tif
            • Copernicus DEM EEA-10 (Europe 10m)
            • SRTM GL1 30m (Global): via OpenTopography or USGS
            • ALOS World 3D 30m: via JAXA or STAC
            • FABDEM (forest/building adjusted): https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn
            
            LANDCOVER:
            ─────────────────────────────────
            • ESA WorldCover 10m (2020, 2021):
              - Direct: https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_NXX_EXXX_Map.tif
              - STAC: Planetary Computer "esa-worldcover" collection
            • Dynamic World (Google, 10m, near real-time): via Earth Engine or STAC
            • NLCD (USA 30m): https://www.mrlc.gov/data
            • CORINE (Europe): https://land.copernicus.eu/pan-european/corine-land-cover
            
            SOIL:
            ─────────────────────────────────
            • SoilGrids v2.0 (Global 250m):
              - WCS: https://maps.isric.org/mapserv?map=/map/soilgrids.map
              - Direct: https://files.isric.org/soilgrids/latest/data/
              Example WCS: gdal_translate "WCS:https://maps.isric.org/mapserv?map=/map/soilgrids.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=soc_0-5cm_mean&SUBSET=X({self.ctx.bbox[0]},{self.ctx.bbox[2]})&SUBSET=Y({self.ctx.bbox[1]},{self.ctx.bbox[3]})&FORMAT=image/tiff" output.tif
            • gNATSGO/gSSURGO (USA): https://nrcs.app.box.com/v/soils
            
            GEOHAZARDS:
            ─────────────────────────────────
            • GEM Global Seismic Hazard Map:
              - WMS: gdal_translate "WMS:https://maps.openquake.org/geoserver/ows?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=gshm:pga_475&BBOX=BBOX&WIDTH=1024&HEIGHT=1024&SRS=EPSG:4326&FORMAT=image/geotiff" output.tif
              - Download: https://downloads.openquake.org/
              - Zenodo: https://zenodo.org/records/8409647
            • USGS Earthquake Hazards: https://earthquake.usgs.gov/hazards/
            • Global Landslide Susceptibility: NASA SEDAC
            
            INFRASTRUCTURE VECTORS (OSM and Other Sources):
            ─────────────────────────────────
            ⚠️ PRESERVE ALL ORIGINAL ATTRIBUTES - COMPUTED FIELDS ADDED AUTOMATICALLY ⚠️
            
            When fetching infrastructure data from ANY source (OSM, government datasets, commercial
            providers, etc.), you MUST preserve ALL original attributes from the source data.
            The processing pipeline will automatically ADD computed fields for dataset compatibility
            without removing any existing attributes.
            
            For OSM data: Use Overpass API with [out:json] format and extract all available tags.
            For other sources: Download and convert to GeoPackage preserving the original schema.
            
            The following schemas show the MINIMUM required fields. Additional source-specific
            fields will be preserved automatically.
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            ROADS SCHEMA (layer name: "roads")
            ═══════════════════════════════════════════════════════════════════════════════════════════
            Required fields:
            • osm_id      (integer) - OSM way ID
            • name        (string)  - Road name from tags.name
            • highway     (string)  - Road type (motorway, trunk, primary, secondary, tertiary, etc.)
            • ref         (string)  - Road reference number (e.g., "I-95", "A1") from tags.ref
            • surface     (string)  - Surface type (paved, asphalt, gravel, etc.) from tags.surface
            • lanes       (string)  - Number of lanes from tags.lanes
            • maxspeed    (string)  - Speed limit from tags.maxspeed
            • oneway      (string)  - One-way flag (yes/no) from tags.oneway
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            RAILWAYS SCHEMA (layer name: "railways")
            ═══════════════════════════════════════════════════════════════════════════════════════════
            Required fields:
            • osm_id      (integer) - OSM way ID
            • name        (string)  - Railway name from tags.name
            • railway     (string)  - Railway type (rail, subway, tram, light_rail) from tags.railway
            • operator    (string)  - Railway operator from tags.operator
            • gauge       (string)  - Track gauge (e.g., "1435") from tags.gauge
            • electrified (string)  - Electrification type (contact_line, rail, yes, no) from tags.electrified
            • usage       (string)  - Usage type (main, branch, industrial) from tags.usage
            • service     (string)  - Service type (spur, yard, siding) from tags.service
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            WATERWAYS SCHEMA (layer name: "waterways")
            ═══════════════════════════════════════════════════════════════════════════════════════════
            Required fields:
            • osm_id           (integer) - OSM way ID
            • name             (string)  - Waterway name from tags.name
            • waterway         (string)  - Waterway type (river, stream, canal, drain, ditch)
            • width            (string)  - Raw width tag from tags.width
            • width_m          (float)   - Width in meters (COMPUTED - see logic below)
            • width_class      (string)  - Classification (COMPUTED: small/medium/large/major)
            • crossing_cost_cat(string)  - Cost category (COMPUTED: low/medium/high/very_high)
            • depth            (string)  - Depth from tags.depth
            • seasonal         (string)  - Seasonal flag from tags.seasonal
            • intermittent     (string)  - Intermittent flag from tags.intermittent
            • tunnel           (string)  - Tunnel flag from tags.tunnel
            
            WIDTH COMPUTATION LOGIC (MUST IMPLEMENT):
            ```python
            # Parse width from tag (e.g., "25 m" -> 25.0)
            width_m = None
            if width_raw:
                try:
                    width_m = float(width_raw.split()[0])
                except:
                    pass
            # Estimate from waterway type if not tagged
            if width_m is None:
                if waterway_type in ('stream', 'ditch'):
                    width_m = 2.0   # 1-3m typical
                elif waterway_type == 'drain':
                    width_m = 5.0   # 3-10m typical
                elif waterway_type == 'canal':
                    width_m = 15.0  # 10-50m typical
                else:  # river
                    width_m = 25.0  # default for rivers
            # Compute width_class and crossing_cost_cat
            if width_m < 3:
                width_class = 'small'
                crossing_cost_cat = 'low'      # $10K-20K open cut
            elif width_m < 10:
                width_class = 'medium'
                crossing_cost_cat = 'medium'   # $30K-70K open cut
            elif width_m < 50:
                width_class = 'large'
                crossing_cost_cat = 'high'     # $200K-400K HDD
            else:
                width_class = 'major'
                crossing_cost_cat = 'very_high' # $800K+ HDD
            ```
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            POWER LINES SCHEMA (layer name: "power_lines")
            ═══════════════════════════════════════════════════════════════════════════════════════════
            Required fields:
            • osm_id        (integer) - OSM way ID
            • name          (string)  - Line name from tags.name
            • power         (string)  - Power type (line, minor_line, cable) from tags.power
            • voltage       (string)  - Raw voltage tag from tags.voltage
            • voltage_v     (integer) - Voltage in volts (COMPUTED from voltage tag)
            • voltage_kv    (float)   - Voltage in kilovolts (COMPUTED: voltage_v / 1000)
            • voltage_class (string)  - Classification (COMPUTED: low/medium/high/extra_high)
            • cables        (string)  - Number of cables from tags.cables
            • operator      (string)  - Operator from tags.operator
            • frequency     (string)  - Frequency from tags.frequency
            • ref           (string)  - Reference from tags.ref
            • crossing_cost (string)  - Cost category (COMPUTED based on voltage_class)
            • location      (string)  - Location (underground, overhead) from tags.location
            
            VOLTAGE COMPUTATION LOGIC:
            ```python
            voltage_v = None
            if voltage_str:
                try:
                    voltage_v = int(voltage_str.replace('kV', '000').replace(' ', ''))
                except:
                    pass
            voltage_kv = voltage_v / 1000 if voltage_v else None
            if voltage_v:
                if voltage_v < 1000:
                    voltage_class = 'low'
                    crossing_cost = 'low'
                elif voltage_v < 50000:
                    voltage_class = 'medium'
                    crossing_cost = 'medium'
                elif voltage_v < 200000:
                    voltage_class = 'high'
                    crossing_cost = 'high'
                else:
                    voltage_class = 'extra_high'
                    crossing_cost = 'very_high'
            ```
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            PIPELINES SCHEMA (layer name: "pipelines")
            ═══════════════════════════════════════════════════════════════════════════════════════════
            Required fields:
            • osm_id      (integer) - OSM way ID
            • name        (string)  - Pipeline name from tags.name
            • man_made    (string)  - Should be "pipeline" from tags.man_made
            • substance   (string)  - Transported substance (gas, oil, water) from tags.substance
            • operator    (string)  - Pipeline operator from tags.operator
            • diameter    (string)  - Pipe diameter from tags.diameter
            • location    (string)  - Location (underground, overground) from tags.location
            • pressure    (string)  - Operating pressure from tags.pressure
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            OSM FETCH WORKFLOW
            ═══════════════════════════════════════════════════════════════════════════════════════════
            1. Query Overpass API with [out:json] format (NOT xml)
            2. Parse JSON response and extract way elements with geometry
            3. For each element, extract tags and compute derived fields per schema above
            4. Build GeoJSON FeatureCollection with proper properties
            5. Convert GeoJSON to GeoPackage with ogr2ogr, setting correct layer name
            6. Layer names MUST match: roads, railways, waterways, power_lines, pipelines
            
            Example Overpass query for roads:
            ```
            curl -s "https://overpass-api.de/api/interpreter" \\
              -d '[out:json][timeout:300];
                  (way["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified|residential|service|track"]({self.ctx.bbox[1]},{self.ctx.bbox[0]},{self.ctx.bbox[3]},{self.ctx.bbox[2]}););
                  out geom;' > roads.json
            ```
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            GDAL VIRTUAL FILE SYSTEMS - ACCESS CLOUD DATA DIRECTLY
            ═══════════════════════════════════════════════════════════════════════════════════════════
            
            /vsicurl/ - Access HTTP/HTTPS URLs directly:
              gdalinfo "/vsicurl/https://example.com/data.tif"
              
            /vsis3/ - Access AWS S3 (public buckets, no credentials needed for public data):
              gdalinfo "/vsis3/copernicus-dem-30m/Copernicus_DSM_COG_10_N44_00_W106_00_DEM/Copernicus_DSM_COG_10_N44_00_W106_00_DEM.tif"
              
            /vsigs/ - Access Google Cloud Storage
            /vsiaz/ - Access Azure Blob Storage
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            CRITICAL RULES
            ═══════════════════════════════════════════════════════════════════════════════════════════
            
            ABSOLUTELY FORBIDDEN:
            ✗ Creating synthetic/placeholder/fake data
            ✗ Generating constant-value rasters to pass validation
            ✗ Substituting lower resolution data without explicit instruction
            ✗ Claiming success when data was not actually fetched
            
            MANDATORY:
            ✓ Only use REAL data from authoritative sources
            ✓ If data is truly unavailable, FAIL with clear explanation of what was tried
            ✓ Validate outputs exist and contain actual data (not empty/corrupt)
            ✓ Follow the resolution/source requested by user
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            PROJECT CONTEXT
            ═══════════════════════════════════════════════════════════════════════════════════════════
            
            Project: {self.ctx.project}
            Project path: {self.ctx.project_path}
            Scripts directory: {scripts_dir} (save reusable scripts here)
            Target CRS: EPSG:{self.ctx.target_epsg} ({self.ctx.target_crs_name})
            AOI bbox (WGS84 minx,miny,maxx,maxy): {self.ctx.bbox_string}
            AOI bbox components: west={self.ctx.bbox[0]}, south={self.ctx.bbox[1]}, east={self.ctx.bbox[2]}, north={self.ctx.bbox[3]}
            Cutline file (for clipping): {self.ctx.cutline_path}
            Data directory: {self.ctx.project_path / "data"}
            
            ═══════════════════════════════════════════════════════════════════════════════════════════
            RESPONSE FORMAT
            ═══════════════════════════════════════════════════════════════════════════════════════════
            
            Always respond with valid JSON:
            {{
                "thinking": "Your analysis: what data is needed, which sources to try, strategy",
                "steps": [
                    {{"description": "Clear description of action", "command": "bash command to execute"}}
                ],
                "post_checks": [
                    {{"description": "Validation description", "command": "bash validation command"}}
                ]
            }}
            """
        ).strip()
    
    def _call_api(self) -> str:
        """Make API call with full conversation history."""
        try:
            content = ""
            stream = self.client.chat.completions.create(
                model=AGENT_MODEL,
                max_tokens=16384,
                messages=[{"role": "system", "content": self.system_prompt}] + self.messages,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content += chunk.choices[0].delta.content

        except Exception as exc:
            error_str = str(exc).lower()
            if "rate" in error_str or "limit" in error_str:
                raise RuntimeError(f"ZEUS AI rate limited. Please retry: {exc}") from exc
            if "timeout" in error_str:
                raise RuntimeError(f"ZEUS AI request timed out. Please retry: {exc}") from exc
            raise RuntimeError(f"ZEUS AI request failed: {exc}") from exc

        if not content:
            raise RuntimeError("ZEUS AI returned an empty response.")
        return content
    
    def add_user_message(self, content: str) -> None:
        """Add a user message to the conversation."""
        self.messages.append({"role": "user", "content": content})
    
    def add_assistant_message(self, content: str) -> None:
        """Add an assistant response to the conversation."""
        self.messages.append({"role": "assistant", "content": content})
    
    def request_plan(self, defn: "DatasetDefinition", override: Optional[str]) -> Dict[str, Any]:
        """Request initial plan for a dataset, with full context of previous operations."""
        protocol_blob = _load_protocol_text()
        metadata_blob = _load_project_metadata_blob(self.ctx)

        previous_context = ""
        if self.completed_datasets or self.failed_datasets:
            previous_context = f"""

            PREVIOUS OPERATIONS IN THIS SESSION:
            - Successfully completed: {', '.join(self.completed_datasets) or 'None yet'}
            - Failed (need alternative approach): {', '.join(self.failed_datasets) or 'None'}
            - Total commands executed so far: {self.total_commands_executed}
            """

        source_guidance = ""
        if defn.key == "dem" and override and _is_3dep_override(override):
            source_guidance = """

            ═══════════════════════════════════════════════════════════════════════════════════════════
            USGS 3DEP 1-METER LIDAR DEM - SPECIFIC INSTRUCTIONS
            ═══════════════════════════════════════════════════════════════════════════════════════════

            You are fetching HIGH-RESOLUTION 1-meter LiDAR DEM from USGS 3DEP. This is the highest
            quality elevation data available for the United States.

            STEP 1: Query TNM API to discover available tiles
            ```bash
            curl -sS "https://tnmaccess.nationalmap.gov/api/v1/products?datasets=Digital%20Elevation%20Model%20(DEM)%201%20meter&bbox=WEST,SOUTH,EAST,NORTH&outputFormat=JSON" > tnm_products.json
            ```

            STEP 2: Parse response and download tiles
            - Each item in "items" array has a "downloadURL" field
            - Download ALL tiles that intersect the AOI
            - Files are typically large (50-500MB each)
            - Use curl with timeouts: curl -sSfL --connect-timeout 60 --max-time 600

            STEP 3: Mosaic and reproject
            - 3DEP tiles are in various UTM projections (NAD83)
            - Use gdalwarp with -te_srs EPSG:4326 to specify bbox CRS
            - Mosaic multiple tiles in one gdalwarp call for efficiency

            FALLBACK: If 3DEP 1m is not available for this area:
            1. Check if 3DEP 10m is available (datasets=Digital%20Elevation%20Model%20(DEM)%2010%20meter)
            2. Fall back to Copernicus GLO-30 (30m global coverage)
            3. NEVER create placeholder/synthetic data

            CRITICAL: The user specifically requested 1-meter resolution. Prioritize finding
            3DEP 1m data. Only fall back to lower resolution if 1m is genuinely unavailable.
            """

        prompt = dedent(
            f"""
            NEW TASK: Fetch and process {defn.label} ({defn.key})

            Dataset details:
            - Type: {defn.dataset_type}
            - Preferred source/override: {override or "Use best available"}
            - Raw output path: {defn.raw_path(self.ctx, override)}
            - Processed output path: {defn.processed_path(self.ctx, override)}
            - Fetch tool (if available): {defn.fetch_tool}
            {previous_context}
            {source_guidance}

            Project metadata:
            {metadata_blob}

            Dataset Fetching Protocol (MUST follow):
            {protocol_blob}

            REQUIREMENTS:
            1. Fetch raw data covering the entire AOI bbox: {self.ctx.bbox_string}
            2. Save raw data to: {defn.raw_path(self.ctx, override)}
            3. Reproject to EPSG:{self.ctx.target_epsg} and clip to AOI using cutline: {self.ctx.cutline_path}
            4. Save processed data to: {defn.processed_path(self.ctx, override)}
            5. DO NOT create symlinks - the Layer Manager reads directly from /processed folders
            6. Validate outputs exist and are not empty

            Provide your plan as JSON with "thinking", "steps", and "post_checks".
            """
        ).strip()

        self.add_user_message(prompt)

        response = self._call_api()
        self.add_assistant_message(response)

        try:
            return _extract_json_payload(response)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ZEUS AI response was not valid JSON: {exc}") from exc
    
    def report_command_result(
        self,
        command: str,
        description: str,
        exit_code: int,
        output: str,
        request_next_steps: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Report the result of a command execution back to ZEUS AI.
        If the command failed and request_next_steps is True, ask for corrective action.
        """
        self.total_commands_executed += 1

        status = "SUCCESS" if exit_code == 0 else f"FAILED (exit code {exit_code})"

        max_output = 6000 if exit_code != 0 else 4000
        if len(output) > max_output:
            head_size = 1500 if exit_code == 0 else 2000
            tail_size = max_output - head_size - 50
            output = output[:head_size] + "\n\n... [truncated] ...\n\n" + output[-tail_size:]

        result_message = dedent(
            f"""
            COMMAND RESULT:
            - Description: {description}
            - Command: {command}
            - Status: {status}
            - Output:
            ```
            {output}
            ```
            """
        ).strip()

        if exit_code == 0:
            if not request_next_steps:
                self.add_user_message(result_message + "\n\nCommand succeeded. Continuing with next step.")
                return None

            self.add_user_message(result_message + "\n\nCommand succeeded. Continue with the next step if any remain, or confirm completion.")
        else:
            self.add_user_message(
                result_message + dedent(
                    """

                    The command failed. Analyze the error THOROUGHLY and provide corrective steps.

                    DIAGNOSTIC CHECKLIST:
                    1. Is this a network/API error? (timeout, 404, rate limit)
                       - Try alternative endpoints or add retries
                    2. Is this a tool availability error?
                       - Check with 'which <tool>' or '<tool> --help'
                    3. Is this a data availability error?
                       - The requested data might not exist for this region
                       - Consider alternative data sources
                    4. Is this a path/permissions error?
                       - Ensure directories exist (mkdir -p)
                       - Check file permissions
                    5. Is this a format/parsing error?
                       - Validate input data format
                       - Check for corrupted downloads

                    CRITICAL: You MUST provide a concrete fix. Do NOT give up.
                    - If one API fails, try another
                    - If one data source is unavailable, find an alternative
                    - NEVER suggest creating placeholder/synthetic data

                    Respond with JSON containing:
                    - "analysis": Your detailed diagnosis of what went wrong
                    - "steps": Array of corrective commands to fix the issue
                    - "post_checks": Optional validation steps after the fix
                    """
                )
            )

        if not request_next_steps:
            return None

        response = self._call_api()
        self.add_assistant_message(response)

        try:
            return _extract_json_payload(response)
        except json.JSONDecodeError:
            return None
    
    def mark_dataset_complete(self, defn: "DatasetDefinition", success: bool) -> None:
        """Record that a dataset has been completed or failed."""
        if success:
            self.completed_datasets.append(defn.label)
            self.add_user_message(f"✓ {defn.label} completed successfully and is ready for use.")
        else:
            self.failed_datasets.append(defn.label)
            self.add_user_message(f"✗ {defn.label} could not be completed after all retry attempts.")
    
    def get_summary(self) -> str:
        """Get a summary of the conversation for logging."""
        return (
            f"ZEUS AI Session Summary:\n"
            f"  - Messages exchanged: {len(self.messages)}\n"
            f"  - Commands executed: {self.total_commands_executed}\n"
            f"  - Datasets completed: {len(self.completed_datasets)}\n"
            f"  - Datasets failed: {len(self.failed_datasets)}"
        )


_AGENT_CONVERSATIONS: Dict[str, AgentConversation] = {}
_AGENT_CONV_LOCK = threading.Lock()


def _get_or_create_conversation(job: "DatasetJobState", ctx: "FetchContext") -> AgentConversation:
    """Get existing conversation for job or create new one."""
    with _AGENT_CONV_LOCK:
        if job.id not in _AGENT_CONVERSATIONS:
            _AGENT_CONVERSATIONS[job.id] = AgentConversation(ctx, job)
        return _AGENT_CONVERSATIONS[job.id]


def _cleanup_conversation(job_id: str) -> None:
    """Remove conversation from memory after job completes."""
    with _AGENT_CONV_LOCK:
        _AGENT_CONVERSATIONS.pop(job_id, None)


ALLOWED_BINARIES = frozenset({
    "gdalwarp", "gdal_translate", "gdalinfo", "gdalbuildvrt", "ogr2ogr",
    "ogrinfo", "gdal_contour", "curl", "wget", "python3", "jq",
    "xmllint", "unzip", "gunzip", "tar", "mkdir", "cp", "mv", "ls",
    "cat", "head", "wc", "echo", "test", "stat", "file", "du",
})

BLOCKED_PATTERNS = frozenset({
    "rm -rf /", "rm -rf /*", "sudo ", "chmod ", "chown ",
    "dd ", "mkfs", "nc ", "ncat ", "netcat ",
    "> /dev/", "| sh", "| bash", "| /bin/",
    "eval ", "exec ",
})


def _validate_agent_command(command: str, ctx: "FetchContext") -> Tuple[bool, str]:
    """Validate an AI-generated command before execution.
    Returns (allowed, reason)."""
    stripped = command.strip()
    if not stripped:
        return False, "Empty command"

    for pattern in BLOCKED_PATTERNS:
        if pattern in stripped:
            return False, f"Blocked pattern detected: {pattern!r}"

    first_token = stripped.split()[0].split("/")[-1]

    from .constants import ZEUS_BIN
    zeus_name = str(ZEUS_BIN).split("/")[-1]
    allowed = ALLOWED_BINARIES | {zeus_name, str(ZEUS_BIN)}

    if first_token not in allowed:
        return False, f"Binary {first_token!r} not in allowlist"

    import shlex
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        tokens = stripped.split()

    allowed_prefixes = (
        str(ctx.project_path),
        "/tmp",
        str(Path(os.getenv("AGRS_DBS_ROOT", "/opt/agrs/DBs"))),
        "/opt/agrs",
    )
    for token in tokens:
        if token.startswith("/") and not any(token.startswith(p) for p in allowed_prefixes):
            if token in ("/dev/null", "/dev/stdout", "/dev/stderr", "/vsicurl/", "/vsis3/", "/vsigs/", "/vsiaz/", "/vsistdout/", "/vsimem/"):
                continue
            if token.startswith("/vsi"):
                continue
            return False, f"Path {token!r} outside allowed directories"

    return True, "OK"


def _run_agent_for_dataset(
    defn: DatasetDefinition,
    ctx: FetchContext,
    job: DatasetJobState,
    override: Optional[str],
    conversation: Optional[AgentConversation] = None,
) -> Optional[List[str]]:
    """
    Run ZEUS AI agent to fetch and process dataset with FULL CONTEXT RETENTION.
    
    The agent maintains complete memory of:
    - All previous datasets processed in this job
    - All commands executed (successes and failures)
    - All outputs from those commands
    - Its own analysis and reasoning
    
    This allows ZEUS AI to learn from errors and adapt its strategy across
    the entire pipeline, not just within a single dataset.
    """
    if not AGENT_ENABLED:
        return None
    
    if conversation is None:
        try:
            conversation = _get_or_create_conversation(job, ctx)
        except RuntimeError as exc:
            _log_to_job(job, ctx, f"ZEUS AI unavailable: {exc}")
            return None
    
    _log_to_job(job, ctx, f"ZEUS AI engaged for {defn.label}.")
    if conversation.completed_datasets:
        _log_to_job(job, ctx, f"ZEUS AI · Context retained from: {', '.join(conversation.completed_datasets)}")
    
    try:
        plan = conversation.request_plan(defn, override)
    except RuntimeError as exc:
        _log_to_job(job, ctx, f"ZEUS AI plan request failed: {exc}")
        conversation.mark_dataset_complete(defn, success=False)
        return None

    executed: List[str] = []
    retry_count = 0
    current_steps = plan.get("steps") or []
    post_checks = plan.get("post_checks") or []
    
    thinking = plan.get("thinking")
    if thinking:
        _log_to_job(job, ctx, f"ZEUS AI thinking: {thinking[:500]}{'...' if len(thinking) > 500 else ''}")
    
    if not current_steps:
        _log_to_job(job, ctx, "ZEUS AI did not provide any executable steps in initial plan.")
        conversation.mark_dataset_complete(defn, success=False)
        return None

    step_idx = 0
    while step_idx < len(current_steps) and len(executed) < AGENT_MAX_STEPS:
        step = current_steps[step_idx]
        if not isinstance(step, dict):
            step_idx += 1
            continue
        
        command = (step.get("command") or "").strip()
        if not command:
            step_idx += 1
            continue
        
        description = (step.get("description") or f"{defn.label} step {step_idx + 1}").strip()
        label = f"ZEUS AI · {description}"

        allowed, reason = _validate_agent_command(command, ctx)
        if not allowed:
            _log_to_job(job, ctx, f"ZEUS AI · BLOCKED command: {reason} — {command[:200]}")
            step_idx += 1
            continue
        
        exit_code, output = _run_command_capture(
            ["/bin/bash", "-lc", command],
            ctx.project_path,
            job,
            ctx,
            label,
        )
        executed.append(command)
        
        is_last_step = step_idx == len(current_steps) - 1 and not post_checks
        
        if exit_code == 0:
            if not is_last_step:
                next_plan = conversation.report_command_result(
                    command=command,
                    description=description,
                    exit_code=exit_code,
                    output=output,
                    request_next_steps=False,
                )
            step_idx += 1
            continue
        
        _log_to_job(job, ctx, f"ZEUS AI · Command failed (exit {exit_code}). Consulting ZEUS AI with full context...")
        
        if retry_count >= AGENT_MAX_RETRIES:
            _log_to_job(
                job,
                ctx,
                f"ZEUS AI · Max retries ({AGENT_MAX_RETRIES}) reached. Cannot complete {defn.label} autonomously.",
            )
            conversation.mark_dataset_complete(defn, success=False)
            raise RuntimeError(f"ZEUS AI exhausted retries for {defn.label}")
        
        retry_count += 1
        _log_to_job(job, ctx, f"ZEUS AI · Retry attempt {retry_count}/{AGENT_MAX_RETRIES}")
        
        try:
            fix_plan = conversation.report_command_result(
                command=command,
                description=description,
                exit_code=exit_code,
                output=output,
                request_next_steps=True,
            )
        except RuntimeError as fix_exc:
            _log_to_job(job, ctx, f"ZEUS AI · Failed to get fix from ZEUS AI: {fix_exc}")
            conversation.mark_dataset_complete(defn, success=False)
            raise RuntimeError(f"ZEUS AI could not recover from error: {fix_exc}")
        
        if not fix_plan:
            _log_to_job(job, ctx, "ZEUS AI · No fix plan returned. Cannot continue.")
            conversation.mark_dataset_complete(defn, success=False)
            raise RuntimeError("ZEUS AI could not provide corrective steps")
        
        analysis = fix_plan.get("analysis")
        if analysis:
            _log_to_job(job, ctx, f"ZEUS AI analysis: {analysis}")
        
        thinking = fix_plan.get("thinking")
        if thinking:
            _log_to_job(job, ctx, f"ZEUS AI thinking: {thinking[:300]}{'...' if len(thinking) > 300 else ''}")
        
        fix_steps = fix_plan.get("steps") or []
        if not fix_steps:
            _log_to_job(job, ctx, "ZEUS AI · No fix steps provided. Cannot continue.")
            conversation.mark_dataset_complete(defn, success=False)
            raise RuntimeError("ZEUS AI could not provide corrective steps")
        
        remaining_original = current_steps[step_idx + 1:]
        current_steps = fix_steps + remaining_original
        step_idx = 0
        
        fix_post_checks = fix_plan.get("post_checks")
        if fix_post_checks:
            post_checks = fix_post_checks + post_checks

    if post_checks and len(executed) < AGENT_MAX_STEPS:
        _log_to_job(job, ctx, "ZEUS AI · Running validation checks...")
        for check_idx, check in enumerate(post_checks):
            if len(executed) >= AGENT_MAX_STEPS:
                break
            if not isinstance(check, dict):
                continue
            command = (check.get("command") or "").strip()
            if not command:
                continue
            description = (check.get("description") or f"Validation {check_idx + 1}").strip()
            label = f"ZEUS AI · {description}"
            
            exit_code, output = _run_command_capture(
                ["/bin/bash", "-lc", command],
                ctx.project_path,
                job,
                ctx,
                label,
            )
            executed.append(command)
            
            conversation.report_command_result(
                command=command,
                description=description,
                exit_code=exit_code,
                output=output,
                request_next_steps=False,
            )
            
            if exit_code != 0:
                _log_to_job(job, ctx, f"ZEUS AI · Validation warning: {description}")

    if len(executed) >= AGENT_MAX_STEPS:
        _log_to_job(job, ctx, f"ZEUS AI · Step limit reached ({AGENT_MAX_STEPS}).")

    if not executed:
        _log_to_job(job, ctx, "ZEUS AI · No commands were executed.")
        conversation.mark_dataset_complete(defn, success=False)
        return None
    
    conversation.mark_dataset_complete(defn, success=True)
    _log_to_job(job, ctx, f"ZEUS AI · Completed {defn.label} with {len(executed)} commands ({retry_count} retries).")
    _log_to_job(job, ctx, f"ZEUS AI · Session: {conversation.total_commands_executed} total commands, {len(conversation.completed_datasets)} datasets done.")
    
    return executed

