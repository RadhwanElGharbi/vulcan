# Global Dataset Coverage Registry

Purpose: Track dataset coverage, access, and temporal characteristics for global geospatial sources used by ZEUS. Scenario-agnostic and updated as sources evolve.

---

## Legend
- **Access**: Open / Registration / Restricted (special permission) / Paid
- **Temporal Extent**: earliest → latest
- **Frequency**: how often new data is recorded/released
- **Coverage**: Global / Regional / National; include notes

---

## Raster Datasets

### Sentinel-2 L2A (MSI)
- **Source**: ESA via Microsoft Planetary Computer Earth Search (Element84)
- **Type**: Multispectral imagery (13 bands, 10/20/60m)
- **Coverage**: Global (land)
- **Access**: Open (no auth)
- **Temporal Extent**: 2015-06 → Present
- **Frequency**: 5 days at equator (constellation)
- **Notes**: Use EarthSearch STAC; download all bands when requested; use closest-date coverage strategy for AOIs.

### Copernicus DEM (GLO-30, GLO-90)
- **Source**: Copernicus (EU)
- **Type**: Global DEM (30m/90m)
- **Coverage**: Global
- **Access**: Registration (open), some regions Restricted historically
- **Temporal Extent**: 2020 → Present (static with revisions)
- **Frequency**: Irregular updates (minor revisions)
- **Notes**: Preferred DEM for routing; 1m LiDAR varies by jurisdiction (see LiDAR section).

### SRTM DEM
- **Source**: NASA
- **Type**: Global DEM (30m)
- **Coverage**: 60°N to 56°S
- **Access**: Open
- **Temporal Extent**: 2000 (static)
- **Frequency**: None (static)
- **Notes**: Use as fallback where Copernicus DEM unavailable/poor.

### ESA WorldCover 10m
- **Source**: ESA (AWS S3)
- **Type**: Land cover classification (10m)
- **Coverage**: Global
- **Access**: Open
- **Temporal Extent**: 2020 → 2021 (yearly snapshots; new years pending)
- **Frequency**: Annual (aim)
- **Notes**: Classes per ESA schema; use STAC to tile-select.

### Global Surface Water (GSW)
- **Source**: JRC/Google
- **Type**: Water occurrence, seasonality, transitions
- **Coverage**: Global
- **Access**: Open
- **Temporal Extent**: 1984 → Present
- **Frequency**: Annual updates
- **Notes**: Use occurrence/seasonality rasters for hydrology constraints.

### WorldPop / GHS-POP
- **Source**: WorldPop; JRC (GHS-POP)
- **Type**: Gridded population density
- **Coverage**: Global (varies by product)
- **Access**: Open
- **Temporal Extent**: 2000 → Present (varies by product)
- **Frequency**: 1–5 years
- **Notes**: Select product based on region and recency.

---

## Vector Datasets

### OpenStreetMap (OSM) - Roads, Railways, Waterways, Landuse, Buildings
- **Source**: OpenStreetMap (Overpass API, planet extracts)
- **Type**: Vector features
- **Coverage**: Global (community completeness varies)
- **Access**: Open
- **Temporal Extent**: 2004 → Present
- **Frequency**: Continuous edits; Overpass reflects near-real-time
- **Notes**: Rate limits apply; cache responsibly. Use bbox/AOI queries.

### WDPA (Protected Planet)
- **Source**: UNEP-WCMC & IUCN
- **Type**: Protected areas polygons/points
- **Coverage**: Global
- **Access**: Registration (open download), some detailed datasets Restricted
- **Temporal Extent**: ~2005 → Present
- **Frequency**: Monthly updates
- **Notes**: Licensing varies by country; check terms per AOI.

### FEMA National Flood Hazard Layer (US)
- **Source**: FEMA
- **Type**: Flood hazard zones
- **Coverage**: United States
- **Access**: Open
- **Temporal Extent**: ~2003 → Present
- **Frequency**: Rolling updates
- **Notes**: Critical for US routing projects.

### National Hydrography Dataset (NHD) (US)
- **Source**: USGS
- **Type**: Hydrography vector
- **Coverage**: United States
- **Access**: Open
- **Temporal Extent**: ~2000 → Present
- **Frequency**: Rolling updates
- **Notes**: High quality; complement OSM.

### National Pipeline Mapping System (NPMS) (US)
- **Source**: PHMSA
- **Type**: Pipelines and LNG plants
- **Coverage**: United States
- **Access**: Restricted (FOUO; access approval required)
- **Temporal Extent**: ~2002 → Present
- **Frequency**: Annual/rolling updates
- **Notes**: Use only with proper permissions; no redistribution.

### LiDAR DEM (1m)
- **Source**: Various national/provincial/state programs (USGS 3DEP, NRCan, state/province portals)
- **Type**: High-resolution elevation (point clouds & rasters)
- **Coverage**: Regional/National (varies)
- **Access**: Open/Restricted (varies by jurisdiction)
- **Temporal Extent**: 2000 → Present (project-based)
- **Frequency**: Project-based; irregular
- **Notes**: Integrate where available; document provider and license in sidecar.

---

## Country/Region Highlights (Non-Exhaustive)

### Canada
- **CanVec / NRCan**: Topographic features (Open) — Rolling updates
- **CGDI**: Various national datasets (Open) — Varies
- **Provincial LiDAR**: Varies by province (Open/Restricted) — Project-based

### United States
- **USGS 3DEP LiDAR**: High-res elevation (Open) — Rolling
- **TIGER/Line (Census)**: Roads/Boundaries (Open) — Annual
- **FEMA NFHL**: Floodplains (Open) — Rolling

### Saudi Arabia / GCC
- **GEO Saudi / Ministry portals**: Limited public access (Restricted) — Varies
- **OSM**: Primary open source for infrastructure (Open) — Continuous

### Europe
- **Copernicus Services**: Land, DEM, Water (Open/Registration) — Rolling
- **EEA**: Environmental datasets (Open) — Varies

---

## Maintenance & Updates
- Version this registry in Git; changes via PRs
- Add new datasets as tools are integrated
- Include citations/links for each entry
- Validate temporal extents and frequencies annually

---

## Appendix: Quick Reference Table

| Dataset | Type | Coverage | Access | Temporal Extent | Frequency |
|---|---|---|---|---|---|
| Sentinel-2 L2A | Raster | Global (land) | Open | 2015-06 → Present | ~5 days |
| Copernicus DEM | Raster | Global | Registration | 2020 → Present | Irregular |
| SRTM DEM | Raster | 60°N–56°S | Open | 2000 (static) | None |
| ESA WorldCover | Raster | Global | Open | 2020 → 2021 | Annual |
| GSW | Raster | Global | Open | 1984 → Present | Annual |
| OSM (roads/rails/water) | Vector | Global | Open | 2004 → Present | Continuous |
| WDPA | Vector | Global | Registration | ~2005 → Present | Monthly |
| FEMA NFHL | Vector | US | Open | ~2003 → Present | Rolling |
| NHD | Vector | US | Open | ~2000 → Present | Rolling |
| WorldPop/GHS-POP | Raster | Global | Open | 2000 → Present | 1–5 years |
| LiDAR DEM | Raster | Regional/National | Varies | 2000 → Present | Project-based |


