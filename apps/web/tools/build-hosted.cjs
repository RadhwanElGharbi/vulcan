const { spawnSync } = require('node:child_process');
require('./copy-cesium.cjs');
const cloud = process.env.VULCAN_DEPLOY_MODE === 'cloud';
const companion = process.env.VULCAN_DEPLOY_MODE === 'companion';
const result = spawnSync(process.execPath, [require.resolve('next/dist/bin/next'), 'build'], {
  stdio: 'inherit', env: { ...process.env, VULCAN_WEB_COMPANION: '1', NEXT_PUBLIC_COMPANION_MODE: companion ? '1' : '0',
    NEXT_PUBLIC_CLOUD_MODE: cloud ? '1' : '0',
    NEXT_PUBLIC_WEB_PREVIEW: !cloud && !companion ? '1' : '0',
    NEXT_PUBLIC_API_URL: companion ? 'http://127.0.0.1:8000/api' : '/api', ZEUS_BUILD_DIR: '.next' }
});
process.exit(result.status ?? 1);
