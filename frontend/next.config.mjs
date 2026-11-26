// The browser only talks to this server; /api is forwarded to the FuelWatch backend (no CORS, one origin).
const backend = process.env.BACKEND_URL || 'http://localhost:8080';

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  compress: false, // a compressed response would hold the server-sent event stream back
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
