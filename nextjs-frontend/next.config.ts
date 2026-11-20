import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === 'development';
const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:5004';

const nextConfig: NextConfig = {
  // Only use rewrites in development for local backend proxy
  // In production (Vercel), use vercel.json rewrites instead
  async rewrites() {
    if (!isDev) return [];
    return [
      {
        source: '/video/:path*',
        destination: `${backendUrl}/video/:path*`,
      },
      {
        source: '/media/:path*',
        destination: `${backendUrl}/media/:path*`,
      },
      {
        source: '/download/:path*',
        destination: `${backendUrl}/download/:path*`,
      },
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },

  // Output standalone for Docker/serverless deployment if needed
  output: process.env.NEXT_OUTPUT === 'standalone' ? 'standalone' : undefined,

  // Optimize images
  images: {
    remotePatterns: [
      { hostname: 'i.ytimg.com' },
      { hostname: 'img.youtube.com' },
    ],
  },
};

export default nextConfig;
