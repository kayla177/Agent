// Types + helpers for the resume-generator tab. Mirrors lib/applications.ts.
// The Python agent (agents/resume_generator) writes these rows; here we only
// read them and let the user view/edit/finalize the generated Markdown and
// manage the experience pool.

export const RESUME_STATUSES = ["draft", "final"] as const;
export type ResumeStatus = (typeof RESUME_STATUSES)[number];

export function isResumeStatus(s: string): s is ResumeStatus {
  return (RESUME_STATUSES as readonly string[]).includes(s);
}

export const DOC_KINDS = ["resume", "project"] as const;
export type DocKind = (typeof DOC_KINDS)[number];

export function isDocKind(s: string): s is DocKind {
  return (DOC_KINDS as readonly string[]).includes(s);
}

export type ExperienceDoc = {
  id: number;
  filename: string;
  kind: string;
  text: string;
  added_at: string;
};

// Lightweight pool-doc summary sent to client components (no heavy `text`).
export type PoolDoc = {
  id: number;
  filename: string;
  kind: string;
  chars: number;
  added_at: string;
};

export type Resume = {
  job_id: string;
  company: string;
  role: string;
  markdown: string;
  keywords: string; // JSON array as stored; use parseKeywords() to decode
  status: string;
  created_at: string;
  updated_at: string;
};

export function parseKeywords(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}

export function nowIso(): string {
  return new Date().toISOString().slice(0, 19);
}
