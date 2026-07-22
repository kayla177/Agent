import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Proxy the agent service (FastAPI, :8001) same-origin. Agent runs + SSE, prefs,
  // file uploads, and all DB mutations (/data/*) — the backend is the single writer;
  // Next.js only reads (via Prisma). Read paths never go through here.
  async rewrites() {
    return [
      { source: "/agents/:path*", destination: "http://127.0.0.1:8001/agents/:path*" },
      { source: "/runs/:path*", destination: "http://127.0.0.1:8001/runs/:path*" },
      { source: "/prefs", destination: "http://127.0.0.1:8001/prefs" },
      { source: "/experience/:path*", destination: "http://127.0.0.1:8001/experience/:path*" },
      { source: "/data/:path*", destination: "http://127.0.0.1:8001/data/:path*" },
    ];
  },
};

export default nextConfig;
