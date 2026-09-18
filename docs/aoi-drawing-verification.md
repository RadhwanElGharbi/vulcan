# AOI drawing repair — 2026-09-18

The MapLibre 6 ESM worker URL was not preserved by the Next.js bundle. Mouse
clicks updated Mapbox Draw's GeoJSON but its source worker never finished, so
vertices and polygons were invisible and closing on the first vertex failed.
MapLibre and Draw CSS imports were also missing.

The build now copies the installed worker and its sibling shared module to
`public/maplibre`, and the AOI map uses that same-origin worker explicitly.
Both CSS files are imported, and Draw uses MapLibre control/canvas class names.
The drawing toolbar waits for initialization and starts in polygon mode.
The toolbar height stays fixed when the live area badge appears, preventing
the map from shifting under the pointer. A stale wizard reset timer is cancelled
when reopening the wizard.

Local Chromium verification using actual mouse and keyboard input passed:

- Four clicked vertices visibly render and close on the first vertex.
- The saved geometry contains exactly four vertices plus its closing coordinate.
- Double-click and Enter both finish a polygon.
- Dragging a vertex changes its coordinate.
- Clear empties the geometry and disables Save Geometry.
- Saving returns to AOI Capture; the local API supplies area and inferred country.
- Both GeoJSON sources finish loading; the canvas uses the restored CSS positioning.

TypeScript validation passed. Project package tests, including offline replay,
passed after adding licence notices to provenance bundles.
