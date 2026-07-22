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
  ghost: number;      // 0 | 1
  also_on: string;    // JSON array string
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

// highest-fit role among status === "new" with a non-null score; null if none
export function bestMatch(jobs: Job[]): Job | null {
  const scored = jobs.filter((j) => j.status === "new" && j.fit_score !== null);
  if (!scored.length) return null;
  return scored.reduce((best, j) => (j.fit_score! > best.fit_score! ? j : best));
}
