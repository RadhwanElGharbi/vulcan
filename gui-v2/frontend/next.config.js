/** @type {import('next').NextConfig} */
const nextConfig = {
  ...(process.env.VULCAN_WEB_COMPANION === '1' ? { output: 'export' } : {}),
  distDir: process.env.ZEUS_BUILD_DIR || '.next',
  // Local discovery can inspect hundreds of retained provider metadata objects.
  // Acquisition itself runs in the durable backend worker.
  experimental: { proxyTimeout: 15 * 60 * 1000 },
  ...(process.env.VULCAN_WEB_COMPANION === '1' ? {} : {
    async rewrites() { return [{source:'/api/:path*', destination:`${process.env.ZEUS_API_URL || 'http://127.0.0.1:8000'}/api/:path*`}] },
  }),
  images: {
    unoptimized: true
  },
  webpack: (config) => {
    config.externals = [...(config.externals || []), { canvas: 'canvas' }];
    return config;
  }
}

module.exports = nextConfig

