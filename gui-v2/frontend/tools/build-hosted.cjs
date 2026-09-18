const { spawnSync } = require('node:child_process');
require('./copy-cesium.cjs');
const cloud = process.env.VULCAN_DEPLOY_MODE === 'cloud';
const result = spawnSync(process.execPath, [require.resolve('next/dist/bin/next'), 'build'], {
  stdio: 'inherit', env: { ...process.env, VULCAN_WEB_COMPANION: '1', NEXT_PUBLIC_COMPANION_MODE: cloud ? '0' : '1',
    NEXT_PUBLIC_CLOUD_MODE: cloud ? '1' : '0',
    NEXT_PUBLIC_API_URL: cloud ? '/api' : 'http://127.0.0.1:8000/api', ZEUS_BUILD_DIR: '.next' }
});
process.exit(result.status ?? 1);
