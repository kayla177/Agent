// Agent UI metadata now comes from the backend (GET /agents, sourced from
// agents/registry.py) — see lib/agents-server.ts. This file holds only the shared
// type and the curated set of agent keys the dashboard features (the other agents
// — resume_generator, gmail_sync — are reached from their own tabs, not the
// dashboard grid).

export type AgentMeta = {
  key: string;
  label: string;
  planet: string;
  emoji: string;
  name: string;
  description: string;
  node_order?: string[];
};

// Which agents appear as run-cards on the dashboard, in order.
export const DASHBOARD_KEYS = [
  "morning_briefing",
  "stock_digest",
  "job_scraper",
  "application_tracker",
] as const;
