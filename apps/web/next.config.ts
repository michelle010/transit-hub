import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@transit-hub/shared"],
  async rewrites() {
    const apiBaseUrl = (process.env.API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
    return [
      {
        // Keep every browser-facing API request same-origin.  API_BASE_URL is
        // read by the Next.js server only and is never a NEXT_PUBLIC_* value.
        source: "/api/:path*",
        destination: `${apiBaseUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
