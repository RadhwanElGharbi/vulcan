const fs = require('node:fs');
const path = require('node:path');
const source = path.join(path.dirname(require.resolve('cesium/package.json')), 'Build', 'Cesium');
const destination = path.join(__dirname, '..', 'public', 'cesium');
fs.mkdirSync(destination, { recursive: true });
for (const name of ['Cesium.js', 'Assets', 'Workers', 'ThirdParty', 'Widgets']) {
  fs.cpSync(path.join(source, name), path.join(destination, name), { recursive: true });
}
fs.copyFileSync(path.join(path.dirname(require.resolve('cesium/package.json')), 'LICENSE.md'), path.join(destination, 'LICENSE.md'));

// MapLibre 6 uses an ESM worker with a sibling module import. Keep both
// unbundled: Next's asset loader does not preserve that worker import graph.
const maplibre = path.join(path.dirname(require.resolve('maplibre-gl/package.json')), 'dist');
const workers = path.join(__dirname, '..', 'public', 'maplibre');
fs.mkdirSync(workers, { recursive: true });
for (const name of ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']) {
  fs.copyFileSync(path.join(maplibre, name), path.join(workers, name));
}
fs.copyFileSync(path.join(maplibre, '..', 'LICENSE.txt'), path.join(workers, 'LICENSE.txt'));
