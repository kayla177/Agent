// Base URL of the FastAPI agent service (:8001). The BROWSER never uses this —
// it hits same-origin proxy paths (/agents/*, /runs/*, /prefs, /data/*, /experience/*)
// via next.config rewrites. This is only for SERVER components, which can't use
// those relative rewrites and must call the service directly. One source of truth,
// overridable via the AGENT_SERVICE_URL env var.
export const AGENT_SERVICE_URL =
  process.env.AGENT_SERVICE_URL ?? "http://127.0.0.1:8001";
