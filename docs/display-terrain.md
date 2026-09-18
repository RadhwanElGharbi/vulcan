# Globe display terrain

The Cesium globe enables background relief automatically. It streams public
[Mapzen Terrarium tiles](https://registry.opendata.aws/terrain-tiles/); no account,
API key, project acquisition or inventory entry is created. Provider credits are
available through Cesium's Data attribution control, linking the
[full source attribution](https://github.com/tilezen/joerd/blob/master/docs/attribution.md).

This is visualization context, not a qualified scientific DEM. It is not included
in exports, provenance bundles, or elevation measurements. The global service
combines sources of varying resolution, dates and vertical references. Web
Mercator relief covers approximately 85 degrees south to 85 degrees north; the
polar caps retain Cesium's ellipsoid surface. Background availability requires
network access. Failed tiles fall back to lower terrain layers or a flat surface,
with a visible notice; toggling terrain retries the requests.

Fetched DEMs are enabled automatically as terrain overlays. The renderer uses
their geographic extent and transparent coverage masks; scientific generations
also use their frozen AOI (including holes) and retained validity mask. Outside
valid coverage the underlying DEM/background remains visible. With overlapping
DEMs, dataset identifiers are sorted ascending and the last valid sample wins.
The imagery layer visibility controls do not change terrain elevation; the
mountain button toggles the composite terrain as a whole.

The display uses a 65 by 65 vertex mesh per requested tile, at most four concurrent
mesh requests, eight concurrent downloads, a 96-tile memory cache, and a ten-second
download timeout. Background source zoom is capped at 12; fetched DEM display
tiles are capped at 18. Deeper views interpolate those source pixels. Shared
world-coordinate sampling keeps adjacent tile edges consistent. Display-only
Terrain-RGB rounds fetched heights to 0.1 metres; original analytical rasters
are unchanged. Raster scale/offset and recognized band height units are applied
when rendering. These display tiles do not perform vertical datum harmonization;
a visible step can occur where DEMs with different heights meet.

Project changes and provider replacement abort pending requests and discard the
previous provider's cache. Scientific artifact/recipe hashes identify frontend
requests, and retained preview identities identify backend tile caches.

Offline verification: `qa/test_display_terrain.py` and
`qa/test_display_terrain.cjs`. These cover coverage, NoData, AOI holes, units,
overlap precedence, shared tile edges and background sampling limits.
