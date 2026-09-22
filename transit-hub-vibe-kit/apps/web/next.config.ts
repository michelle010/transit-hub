import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@transit-hub/shared"],
  async rewrites() {
    const apiBaseUrl = (process.env.API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
    return [
      {
        source: "/api/cities/search",
        destination: `${apiBaseUrl}/api/cities/search`,
      },
      {
        source: "/api/cities/:cityId/hubs",
        destination: `${apiBaseUrl}/api/cities/:cityId/hubs`,
      },
      {
        source: "/api/transfer/evaluate",
        destination: `${apiBaseUrl}/api/transfer/evaluate`,
      },
    ];
  },
};

export default nextConfig;
