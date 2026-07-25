import { AGENT_SERVICE_URL } from "./agent-service";
import type { AgentMeta } from "./agents";

// Server-only: fetch the agent metadata from the backend registry (single source
// of truth). Returns [] if the agent service is offline, so callers can render a
// graceful "service offline" state instead of crashing.
export async function getAgents(): Promise<AgentMeta[]> {
  try {
    const res = await fetch(`${AGENT_SERVICE_URL}/agents`, { cache: "no-store" });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.agents ?? []) as AgentMeta[];
  } catch {
    return [];
  }
}
