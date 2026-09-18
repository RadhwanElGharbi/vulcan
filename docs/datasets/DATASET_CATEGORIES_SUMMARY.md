# Dataset categories

| Category | Default source | Output |
| --- | --- | --- |
| Elevation | Copernicus DEM GLO-30 | GeoTIFF |
| Land cover | ESA WorldCover 2021, 10 m | GeoTIFF |
| Soil | ISRIC SoilGrids SOC, 0–5 cm, 250 m | GeoTIFF |
| Seismic hazard | GEM 2023 PGA, 475-year return period | GeoTIFF |
| Roads | OpenStreetMap | GeoPackage |
| Railways | OpenStreetMap | GeoPackage |
| Power lines | OpenStreetMap | GeoPackage |
| Waterways | NHN in Canada; OpenStreetMap elsewhere | GeoPackage |
| Existing pipelines | CER in Canada; OpenStreetMap elsewhere | GeoPackage |
| Protected areas | CPCAD, Canada | GeoPackage |
| Indigenous lands | CLSS, Canada | GeoPackage |

The AOI is also available as GeoJSON and a GeoPackage layer. Each fetched dataset has a raw artifact, a processed artifact in the project CRS, and metadata sidecars. Categories can be fetched independently.
