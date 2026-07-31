export const STATUSES = ["applied", "interview", "offer", "accepted", "rejected"] as const;
export type Status = (typeof STATUSES)[number];

export function isStatus(s: string): s is Status {
  return (STATUSES as readonly string[]).includes(s);
}

export type Application = {
  id: number;
  company: string;
  role: string;
  url: string;
  status: string;
  applied_date: string;
  updated_date: string;
  notes: string;
  auto_detected: number; // SQLite int, 0 | 1
  resume_job_id: string | null; // which generated résumé was used to apply
};

// Per-status tint for the pill. The status WORD is always rendered beside it, so
// identity is text-carried (a labeled status indicator, not color-alone).
export const STATUS_META: Record<Status, { label: string; color: string }> = {
  applied:   { label: "applied",   color: "#4a90ff" },
  interview: { label: "interview", color: "#e0b15a" },
  offer:     { label: "offer",     color: "#b98cff" },
  accepted:  { label: "accepted",  color: "#7fc08a" },
  rejected:  { label: "rejected",  color: "#e0705a" },
};

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export type Stats = {
  total: number;
  active: number;       // applied + interview + offer
  interviews: number;
  responseRate: number; // % that got any response (status past "applied")
  counts: Record<Status, number>;
};

export function computeStats(apps: { status: string }[]): Stats {
  const counts = Object.fromEntries(STATUSES.map((s) => [s, 0])) as Record<Status, number>;
  for (const a of apps) if (isStatus(a.status)) counts[a.status] += 1;
  const total = apps.length;
  const active = counts.applied + counts.interview + counts.offer;
  const responses = counts.interview + counts.offer + counts.accepted + counts.rejected;
  const responseRate = total ? Math.round((responses / total) * 100) : 0;
  return { total, active, interviews: counts.interview, responseRate, counts };
}
