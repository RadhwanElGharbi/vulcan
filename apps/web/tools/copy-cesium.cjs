const fs = require('node:fs');
const path = require('node:path');
const source = path.join(path.dirname(require.resolve('cesium/package.json')), 'Build', 'Cesium');
const destination = path.join(__dirname, '..', 'public', 'cesium');
fs.mkdirSync(destination, { recursive: true });
for (const name of ['Cesium.js', 'Assets', 'Workers', 'ThirdParty', 'Widgets']) {
  fs.cpSync(path.join(source, name), path.join(destination, name), { recursive: true });
}
