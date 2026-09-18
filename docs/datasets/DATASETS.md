# AGRS ZEUS Dataset Documentation

This document provides comprehensive information about all datasets supported by AGRS ZEUS, their tools, and available bands/categories.

## Dataset Overview
Per-country coverage registry:

- Index: `docs/DATASET_COVERAGE_BY_COUNTRY.md`
- Wide table (per country): `docs/coverage/COUNTRY_COVERAGE.csv`
- Long form (per country × dataset record): `docs/coverage/COUNTRY_COVERAGE_LONG.csv`


| Dataset | Tool | Provider | Bands/Categories | Resolution | Coverage |
|---------|------|----------|------------------|------------|----------|
| **Sentinel-2 L2A** | `tools sentinel2_fetch` | Microsoft Planetary Computer (EarthSearch STAC) | 13 spectral bands + auxiliary data | 10m, 20m, 60m | Global |
| **DEM (Digital Elevation Model)** | `tools dem_fetch` | Multiple providers (USGS, SRTM, Copernicus) | Single elevation band | 1m, 10m, 30m | Global |
| **Copernicus Products** | `tools copernicus_fetch` | CDSE (Copernicus Data Space Ecosystem) | Sentinel-1 SAR, Sentinel-3, Land Cover | Variable | Global |

---

## Sentinel-2 L2A Dataset

**Tool:** `tools sentinel2_fetch`  
**Provider:** Microsoft Planetary Computer (EarthSearch STAC)  
**Collection:** `sentinel-2-l2a`  
**Data Type:** Bottom-of-atmosphere reflectance (surface reflectance)  
**Coverage:** Global, every 5 days  
**Format:** Cloud Optimized GeoTIFF (COG)

### Available Bands

| Band | Wavelength | Resolution | Description | Common Uses |
|------|------------|------------|-------------|-------------|
| **B01** | 443 nm | 60m | Coastal aerosol | Atmospheric correction, coastal studies |
| **B02** | 490 nm | 10m | Blue | Water bodies, atmospheric correction |
| **B03** | 560 nm | 10m | Green | Vegetation, water bodies |
| **B04** | 665 nm | 10m | Red | Vegetation, soil, urban areas |
| **B05** | 705 nm | 20m | Red Edge 1 | Vegetation stress, chlorophyll content |
| **B06** | 740 nm | 20m | Red Edge 2 | Vegetation structure, leaf area index |
| **B07** | 783 nm | 20m | Red Edge 3 | Vegetation biomass, canopy structure |
| **B08** | 842 nm | 10m | NIR | Vegetation health, water content |
| **B8A** | 865 nm | 20m | NIR narrow | Vegetation analysis, biomass |
| **B09** | 945 nm | 60m | Water vapor | Atmospheric correction |
| **B10** | 1380 nm | 60m | Cirrus | Cloud detection, atmospheric correction |
| **B11** | 1610 nm | 20m | SWIR 1 | Soil moisture, vegetation water content |
| **B12** | 2190 nm | 20m | SWIR 2 | Soil properties, mineral mapping |

### Auxiliary Data

| Product | Description | Use Case |
|---------|-------------|----------|
| **SCL** | Scene Classification Layer | Cloud/cloud shadow detection, land cover classification |
| **TCI** | True Color Image | Visual interpretation, RGB composite |
| **AOT** | Aerosol Optical Thickness | Atmospheric correction quality |
| **WVP** | Water Vapor | Atmospheric correction |
| **VIS** | Visibility | Atmospheric correction |

### Band Groups (Predefined Combinations)

| Group | Bands | Description |
|-------|-------|-------------|
| **visual** | B02, B03, B04 | True color RGB |
| **nir** | B08, B8A | Near-infrared bands |
| **rededge** | B05, B06, B07 | Red edge bands for vegetation |
| **swir** | B11, B12 | Short-wave infrared bands |
| **atmospheric** | B01, B09, B10 | Atmospheric correction bands |
| **standard** | B02, B03, B04, B08 | Most commonly used bands |
| **all** | All 13 spectral bands | Complete spectral information |

### CLI Usage Examples

```bash
# Fetch specific bands
./build/zeus tools sentinel2_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --bands B03,B08 -o /output/dir

# Fetch all bands
./build/zeus tools sentinel2_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --all-bands -o /output/dir

# Fetch predefined band group
./build/zeus tools sentinel2_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --band-groups visual,nir -o /output/dir

# Fetch with auxiliary data
./build/zeus tools sentinel2_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --bands B02,B03,B04,B08 --auxiliary SCL,TCI -o /output/dir
```

---

## Copernicus Products Dataset

**Tool:** `tools copernicus_fetch`  
**Provider:** Copernicus Data Space Ecosystem (CDSE)  
**Authentication:** Required (CDSE username/password)  
**Status:** Placeholder for future implementation

### Planned Products

| Product | Description | Use Case | Status |
|---------|-------------|----------|---------|
| **S1GRD** | Sentinel-1 SAR Ground Range Detected | All-weather imaging, terrain displacement | Planned |
| **S3OLCI** | Sentinel-3 Ocean and Land Colour Instrument | Ocean color, water quality | Planned |
| **S3SLSTR** | Sentinel-3 Sea and Land Surface Temperature Radiometer | Sea/land surface temperature | Planned |
| **LANDCOVER** | Copernicus Land Monitoring Service | Land cover classification | Planned |

### CLI Usage Examples

```bash
# Sentinel-1 SAR (planned)
./build/zeus tools copernicus_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --product S1GRD --username "user@email.com" --password "pass" -o /output/dir

# Sentinel-3 OLCI (planned)
./build/zeus tools copernicus_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --product S3OLCI --username "user@email.com" --password "pass" -o /output/dir

# Land Cover (planned)
./build/zeus tools copernicus_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --product LANDCOVER --username "user@email.com" --password "pass" -o /output/dir
```

### Current Status

**Note:** This tool is currently a placeholder. All Copernicus products are planned for future implementation.

**For Sentinel-2 data, use:** `tools sentinel2_fetch` (fully functional)

---

## Multi-tile Mosaicking

**Tool:** `tools mosaic`  
**Purpose:** Combine multiple raster tiles into a single seamless mosaic  
**Format:** Cloud Optimized GeoTIFF (COG)  
**Use Cases:** Large AOI coverage, multi-tile datasets, seamless composites

### Features

- **Multiple Input Support:** Combine any number of raster files
- **Automatic Clipping:** Optional bbox or cutline clipping
- **CRS Reprojection:** Target any coordinate reference system
- **Resampling Options:** Bilinear, nearest, cubic, etc.
- **COG Output:** Cloud-optimized GeoTIFF format
- **Metadata Preservation:** Maintains source information

### CLI Usage Examples

```bash
# Basic mosaicking
./build/zeus tools mosaic input1.tif input2.tif input3.tif output_mosaic.tif

# Mosaic with bbox clipping
./build/zeus tools mosaic input1.tif input2.tif output_mosaic.tif --bbox "-104.9,44.2,-104.6,44.5"

# Mosaic with cutline clipping
./build/zeus tools mosaic input1.tif input2.tif output_mosaic.tif --cutline aoi.geojson

# Mosaic with custom CRS and resampling
./build/zeus tools mosaic input1.tif input2.tif output_mosaic.tif --crs EPSG:3857 --resampling cubic
```

### Integration with Data Fetching

The mosaic tool is designed to work seamlessly with data fetching tools:

```bash
# Fetch multiple tiles and mosaic them
./build/zeus tools s2_fetch --bbox "..." --datetime "..." --all-bands -o /tmp/tiles/
./build/zeus tools mosaic /tmp/tiles/*.tif final_mosaic.tif --cutline aoi.geojson
```

---

## DEM (Digital Elevation Model) Dataset

**Tool:** `tools dem_fetch`  
**Providers:** Multiple (USGS, SRTM, Copernicus, OpenTopo)  
**Data Type:** Digital elevation data  
**Coverage:** Global  
**Format:** Cloud Optimized GeoTIFF (COG)

### Available Providers and Resolutions

| Provider | Resolution | Coverage | Data Source | Best For |
|----------|------------|----------|-------------|----------|
| **USGS 1m** | 1m | USA | USGS 3DEP | High-resolution terrain analysis |
| **SRTM** | 30m | Global (60°N to 60°S) | NASA Shuttle Radar | Global applications |
| **Copernicus** | 30m | Global | Copernicus DEM | European focus, global coverage |
| **OpenTopo** | 30m | Global | SRTM + ASTER GDEM | Open source alternative |

### CLI Usage Examples

```bash
# Fetch 1m DEM from USGS
./build/zeus tools dem_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --res 1m --provider usgs -o dem_1m.tif

# Fetch 30m SRTM DEM
./build/zeus tools dem_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --res 30m --provider srtm -o dem_30m.tif

# Auto-select best provider
./build/zeus tools dem_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --res 30m --provider auto -o dem_auto.tif
```

---

## Copernicus Data Space Ecosystem (CDSE)

**Tool:** `tools copernicus_fetch`  
**Provider:** CDSE (Copernicus Data Space Ecosystem)  
**Data Types:** Multiple Copernicus products  
**Coverage:** Global  
**Format:** Various (GeoTIFF, NetCDF, etc.)

### Available Products

| Product | Description | Bands/Categories |
|---------|-------------|------------------|
| **S2L2A** | Sentinel-2 Level-2A | All Sentinel-2 L2A bands |
| **S2L1C** | Sentinel-2 Level-1C | All Sentinel-2 L1C bands |
| **S1GRD** | Sentinel-1 Ground Range Detected | VV, VH polarizations |
| **S3OLCI** | Sentinel-3 OLCI | Ocean color bands |
| **S3SLSTR** | Sentinel-3 SLSTR | Sea/land surface temperature |

### CLI Usage Examples

```bash
# Fetch Sentinel-2 L2A via CDSE
./build/zeus tools copernicus_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --product S2L2A --username "your@email.com" --password "your_password" -o /output/dir

# Fetch Sentinel-1 data
./build/zeus tools copernicus_fetch --bbox "-104.945278,44.248889,-104.659722,44.494444" --datetime "2024-04-20" --product S1GRD --username "your@email.com" --password "your_password" -o /output/dir
```

---

## Implementation Status

| Tool | Status | Features | Notes |
|------|--------|----------|-------|
| `s2_fetch` | ✅ Implemented | Basic B03/B08 only | **NEEDS UPDATE** for all bands |
| `dem_fetch` | ✅ Implemented | Multiple providers, resolutions | Complete |
| `copernicus_fetch` | ✅ Implemented | Multiple products | Complete |
| `mosaic` | ✅ Implemented | Multi-file mosaicking | Complete |

---

## Next Steps

1. **Update `s2_fetch`** to support all Sentinel-2 L2A bands with flexible selection
2. **Add band group support** for common combinations
3. **Implement auxiliary data fetching** (SCL, TCI, etc.)
4. **Add validation** for band combinations
5. **Create dataset-specific documentation** for each tool

---

## Data Quality and Processing

All datasets are processed with:
- **Cloud Optimized GeoTIFF (COG)** output format for optimal performance
- **Sidecar metadata** in JSON format for provenance tracking
- **Automatic CRS handling** with UTM projection for analysis
- **Quality filtering** based on cloud cover and data availability
- **Mosaicking support** for multi-tile coverage

---

*Last Updated: September 25, 2024*

