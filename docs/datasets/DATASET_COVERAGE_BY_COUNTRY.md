# Per-Country Dataset Coverage Registry

Purpose: Provide country-by-country coverage for key datasets used by ZEUS, with source, data type, accessibility, temporal extent, and update frequency. Scenario-agnostic; intended for global use.

Download the Excel workbook (one sheet per country): `docs/coverage/COUNTRY_COVERAGE_BY_COUNTRY.xlsx`

- Registry file (machine-readable): `docs/coverage/COUNTRY_COVERAGE.csv`
- This index documents dataset properties and the coverage legend used in the CSV.

---

## Datasets (Properties)

- Sentinel-2 L2A (MSI)
  - Source: ESA via Microsoft Planetary Computer Earth Search (Element84)
  - Type: Multispectral imagery (13 bands, 10/20/60m)
  - Access: Open (no auth)
  - Temporal Extent: 2015-06 → Present
  - Frequency: ~5-day revisit at equator

- Copernicus DEM (GLO-30/GLO-90)
  - Source: Copernicus
  - Type: Global DEM (30m/90m)
  - Access: Registration (open)
  - Temporal Extent: 2020 → Present (static with revisions)
  - Frequency: Irregular revisions

- ESA WorldCover 10m
  - Source: ESA (AWS S3)
  - Type: Land cover classification
  - Access: Open
  - Temporal Extent: 2020 → 2021
  - Frequency: Annual (aim)

- Global Surface Water (GSW)
  - Source: JRC/Google
  - Type: Water occurrence/seasonality/transitions
  - Access: Open
  - Temporal Extent: 1984 → Present
  - Frequency: Annual updates

- OpenStreetMap (OSM)
  - Source: OpenStreetMap / Overpass API
  - Type: Roads, railways, waterways, landuse, buildings
  - Access: Open
  - Temporal Extent: 2004 → Present
  - Frequency: Continuous edits (near real-time)

- WDPA (Protected Planet)
  - Source: UNEP-WCMC & IUCN
  - Type: Protected areas polygons/points
  - Access: Registration (open; some restrictions by country)
  - Temporal Extent: ~2005 → Present
  - Frequency: Monthly updates

- WorldPop / GHS-POP
  - Source: WorldPop; JRC (GHS-POP)
  - Type: Gridded population density
  - Access: Open
  - Temporal Extent: 2000 → Present (varies by product)
  - Frequency: 1–5 years

- FEMA NFHL (US only)
  - Source: FEMA
  - Type: Flood hazard zones
  - Access: Open
  - Temporal Extent: ~2003 → Present
  - Frequency: Rolling updates

- NHD (US only)
  - Source: USGS
  - Type: Hydrography vector
  - Access: Open
  - Temporal Extent: ~2000 → Present
  - Frequency: Rolling updates

- NPMS (US only)
  - Source: PHMSA
  - Type: Pipeline locations
  - Access: Restricted (approval required)
  - Temporal Extent: ~2002 → Present
  - Frequency: Annual/rolling updates

- LiDAR DEM 1m (jurisdictional)
  - Source: National/provincial/state programs
  - Type: High-resolution elevation (rasters/point clouds)
  - Access: Open/Restricted (varies)
  - Temporal Extent: 2000 → Present (project-based)
  - Frequency: Project-based

---

## Coverage Legend (CSV values)
- Available: Dataset is available/applicable for the country
- Restricted: Dataset exists but requires special permission
- N/A: Dataset not applicable to the country (e.g., US-only datasets abroad)
- Varies: Availability exists but varies within country (e.g., LiDAR programs)

---

## Notes
- Global datasets (Sentinel-2, Copernicus DEM, WorldCover, GSW, OSM, WDPA, WorldPop/GHS-POP) are generally Available in all countries (land areas), barring local access restrictions.
- US-only datasets (FEMA NFHL, NHD, NPMS) are Available only in the United States; elsewhere they are marked N/A.
- LiDAR 1m availability is program-specific and varies within and across countries.
- For updates or corrections, edit `COUNTRY_COVERAGE.csv` and submit a change.


