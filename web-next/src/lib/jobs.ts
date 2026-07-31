export const JOB_STATUSES = ["new", "viewed", "applied", "dismissed"] as const;

// Per-status pill tint (the status word is always shown, so color is decorative).
export const JOB_STATUS_META: Record<string, { label: string; color: string }> = {
  new: { label: "new", color: "#7fb0ff" },
  viewed: { label: "viewed", color: "#8b93a6" },
  applied: { label: "applied", color: "#7fc08a" },
  dismissed: { label: "dismissed", color: "#e0705a" },
};

export type Job = {
  id: string;
  company: string;
  title: string;
  location: string;
  url: string;
  status: string;
  ats: string;
  posted_at: string | null;
  remote: number | null;
  compensation: string | null;
  department: string | null;
  description: string | null;
  fit_score: number | null;
  fit_reason: string | null;
  ghost: number;         // 0 | 1
  ghost_reason: string;  // WHY it is flagged; "" when it is not
  also_on: string;       // JSON array string
  country: string;       // US | CA | OTHER | UNKNOWN
  eligible: number;         // 0 | 1 — undergrad-eligibility screen (rank.py)
  eligible_reason: string;  // WHY the screen said no; "" when eligible
};

export type FitTier = "hi" | "mid" | "lo" | "none";

export function fitTier(score: number | null): FitTier {
  if (score === null || score === undefined) return "none";
  if (score >= 75) return "hi";
  if (score >= 50) return "mid";
  return "lo";
}

export const FIT_COLOR: Record<FitTier, string> = {
  hi: "#7fc08a", mid: "#e0b15a", lo: "#e0705a", none: "#8b93a6",
};

export function ageDays(postedAt: string | null): number | null {
  if (!postedAt) return null;
  const d = new Date(postedAt.slice(0, 10) + "T00:00:00Z");
  if (isNaN(d.getTime())) return null;
  return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86_400_000));
}

export function parseAlsoOn(s: string): string[] {
  try {
    const a = JSON.parse(s);
    return Array.isArray(a) ? a.map(String) : [];
  } catch {
    return [];
  }
}

// fit desc, unscored (null) last
export function byFitDesc(a: Job, b: Job): number {
  if (a.fit_score === null && b.fit_score === null) return 0;
  if (a.fit_score === null) return 1;
  if (b.fit_score === null) return -1;
  return b.fit_score - a.fit_score;
}

export function byDateDesc(a: Job, b: Job): number {
  return (b.posted_at ?? "").localeCompare(a.posted_at ?? "");
}

// Recency bucket: fresher = lower number = higher priority. Undated (unknown age)
// sinks to the bottom alongside the oldest roles.
export function ageBucket(j: Job): number {
  const a = ageDays(j.posted_at);
  if (a === null) return 3;
  if (a <= 2) return 0;   // last ~48h
  if (a <= 7) return 1;   // this week
  if (a <= 30) return 2;  // this month
  return 3;               // older
}

// Default ordering: freshest bucket first, then best fit within the bucket — so
// a 24h role always outranks a 300d one, but fit still breaks ties.
export function byPriority(a: Job, b: Job): number {
  const d = ageBucket(a) - ageBucket(b);
  return d !== 0 ? d : byFitDesc(a, b);
}

// True when the undergrad-eligibility screen rejected this posting. Defaults to
// ELIGIBLE for anything missing/legacy: `eligible` is NOT NULL DEFAULT 1 in the
// schema, and a row must only ever be hidden by an explicit judgement.
export function isScreenedOut(job: Job): boolean {
  return job.eligible === 0;
}

// Short badge label for a screened-out posting, mirroring `ghostLabel`. The full
// `eligible_reason` is shown on hover; rows tagged before a reason was recorded
// fall back to fixed wording so the badge is never blank.
export function eligibleLabel(job: Job): string {
  return (job.eligible_reason || "").trim() || "not undergrad-eligible";
}

// Highest-fit role among status === "new" with a non-null score, preferring
// reasonably fresh roles (≤30d) so the hero never highlights a stale posting.
//
// Screened-out rows are excluded unconditionally — NOT via the board's toggle.
// The hero is a single "apply to this next" recommendation, so it must never
// advertise a posting that is hidden from the list underneath it; that would be
// the UI contradicting itself, and there would be no row to click through to.
export function bestMatch(jobs: Job[]): Job | null {
  const scored = jobs.filter(
    (j) => j.status === "new" && j.fit_score !== null && !isScreenedOut(j),
  );
  if (!scored.length) return null;
  const fresh = scored.filter((j) => ageBucket(j) <= 2);
  const pool = fresh.length ? fresh : scored;
  return pool.reduce((best, j) => (j.fit_score! > best.fit_score! ? j : best));
}

export const COUNTRY_LABEL: Record<string, string> = {
  US: "US", CA: "Canada", OTHER: "intl", UNKNOWN: "?",
};

export const ALL_COUNTRIES = ["US", "CA", "OTHER", "UNKNOWN"];

// Mirrors config.JOB_COUNTRIES's default EXACTLY, used only when the effective
// pref cannot be read. UNKNOWN is deliberately absent here because
// `visibleCountries` adds it unconditionally — the two lists used to disagree
// (config said ["US","CA"], this said ["US","CA","UNKNOWN"]).
export const DEFAULT_COUNTRIES = ["US", "CA"];

// The board's initial country filter, from the effective JOB_COUNTRIES pref.
// UNKNOWN is ALWAYS included regardless of the pref: an unclassifiable location
// is never dropped by the scraper (see locations.py) and must never be silently
// hidden either, or a real US role with a location string like "2 Locations"
// would vanish from the board with no way to find it. An empty pref means
// "no filtering" everywhere else in this repo (JOB_SOURCES, STOCK_WATCHLIST),
// so it falls back to the default rather than showing nothing.
export function visibleCountries(pref: string[] | null | undefined): string[] {
  const base = pref && pref.length ? pref : DEFAULT_COUNTRIES;
  return Array.from(new Set([...base, "UNKNOWN"]));
}

export function inCountries(job: Job, allowed: string[]): boolean {
  return allowed.includes(job.country || "UNKNOWN");
}

// Short badge label for a flagged posting. `ghost_reason` is written by
// freshness_node / store.sweep_ghosts; the full string is shown on hover. Rows
// flagged before ghost_reason was mirrored fall back to the old "stale Nd".
export function ghostLabel(job: Job, age: number | null): string {
  const r = (job.ghost_reason || "").trim();
  if (r.startsWith("delisted")) return "removed from board";
  if (r.startsWith("deadline passed")) return "deadline passed";
  return age !== null ? `stale ${age}d` : "stale";
}
