export type AgentMeta = {
  key: string; label: string; planet: string; emoji: string; name: string; description: string;
};

export const AGENTS: AgentMeta[] = [
  { key: "morning_briefing", label: "briefing", planet: "earth", emoji: "🌅", name: "Morning Briefing", description: "Weather, commute & traffic, calendar, and news catch-up." },
  { key: "stock_digest", label: "stocks", planet: "jupiter", emoji: "📈", name: "Stock Digest", description: "Quotes, technical indicators, and news sentiment (info only, not advice)." },
  { key: "job_scraper", label: "jobs", planet: "mars", emoji: "🧑‍💻", name: "Job Scraper", description: "New co-op / intern / new-grad roles from official ATS boards." },
  { key: "application_tracker", label: "tracker", planet: "saturn", emoji: "📋", name: "Application Tracker", description: "Your application pipeline, follow-up reminders, and interviews." },
];
