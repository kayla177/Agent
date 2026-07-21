import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Proxy agent-run + SSE endpoints to the (future) slim FastAPI on :8001.
  // Inert until Plan 5 moves FastAPI to :8001; harmless before then.
  async rewrites() {
    return [
      { source: "/agents/:path*", destination: "http://127.0.0.1:8001/agents/:path*" },
      { source: "/runs/:path*", destination: "http://127.0.0.1:8001/runs/:path*" },
      { source: "/prefs", destination: "http://127.0.0.1:8001/prefs" },
    ];
  },
};

export default nextConfig;
