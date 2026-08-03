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

// ---------------------------------------------------------------------------
// Assisted apply — the agent fills the form, you submit it
// ---------------------------------------------------------------------------
//
// The one rule the whole feature is built around: NO code path submits an
// application. Nothing in this file, and nothing in ApplyModal, may ever offer to
// press Submit — the agent fills, the human sends. `tests/test_applier_ui.py`
// scans the modal for that.

// The three boards the DOM locator was written and measured against.
//
// MIRRORS `agents.job_applier.nodes.load_profile.SUPPORTED_ATS`, and the mirror
// is pinned: `test_the_boards_the_ui_offers_autofill_for_are_the_ones_the_agent_supports`
// reads this array out of this file and fails if the two drift. Offering autofill
// for a fourth board would put a button in front of the user that the agent
// refuses at its first node — a guess about a DOM nobody has looked at, typed
// into a real employer's form.
export const AUTOFILL_ATS = ["greenhouse", "lever", "ashby"] as const;

export function supportsAutofill(ats: string | null | undefined): boolean {
  return (AUTOFILL_ATS as readonly string[]).includes((ats ?? "").trim().toLowerCase());
}

// Why the button is not there. Phase A's manual flow is the answer for every
// other board, and saying so beats an absent control the user cannot ask about.
export function autofillUnavailableNote(ats: string | null | undefined): string {
  const board = (ats ?? "").trim().toLowerCase();
  const boards = AUTOFILL_ATS.join(", ");
  return board
    ? `Autofill can only read ${boards} application forms, and this posting is on ${board}. Open the posting and fill it in yourself.`
    : `Autofill can only read ${boards} application forms, and this posting's board is unknown. Open the posting and fill it in yourself.`;
}

// The handoff report, as `agents/job_applier/nodes/handoff.py` builds it and
// `GET /data/jobs/assisted-apply/report` serves it. Structured data, NOT the
// rendered text: the bands below are ordered here, and a UI that parsed
// `render_text()` would break the first time a heading was reworded.

// Render order. Mirrors `handoff.GROUPS` — blocking first because it is the only
// band that costs you the application if you miss it, `done` last because it
// exists to be skimmed. Pinned against the Python tuple by
// `test_the_ui_bands_match_the_report_bands`.
export const HANDOFF_GROUPS = ["blocking", "review", "withheld", "done"] as const;
export type HandoffGroup = (typeof HANDOFF_GROUPS)[number];

export const HANDOFF_GROUP_LABEL: Record<HandoffGroup, string> = {
  blocking: "Required and still empty — the form will not submit until you fill these",
  review: "Needs your review — the agent put something here, or could not",
  withheld: "Left untouched on purpose — voluntary self-identification",
  done: "Filled and verified — nothing to do, open it to spot-check",
};

// Why an item is where it is. The KEYS mirror `handoff.REASONS` and are pinned by
// `test_the_ui_knows_every_reason_the_report_can_emit`; the wording is the UI's
// own (the text report has room for a sentence, a badge does not). The three
// "left alone" reasons stay distinct here for the same reason they do there —
// "find it yourself" and "confirm it yourself" are different jobs.
export const HANDOFF_REASON_TAG: Record<string, string> = {
  refused: "yours to answer",
  unreadable: "no readable label — find it on the page",
  withheld_eeo: "not touched on purpose",
  empty: "not filled",
  changed: "the page rewrote your value",
  drafted: "AI-DRAFTED — read every word",
  filled: "filled",
  attached: "attached",
};

export type HandoffItem = {
  key: string;
  label: string;
  group: string;
  reason: string;
  section: string;
  required: boolean;
  status: string;
  value: string;
  intended: string;
  suggestion: string;
  note: string;
  drafted: boolean;
  kind: string;
};

export type HandoffReport = {
  items: HandoffItem[];
  job_title: string;
  company: string;
  form_url: string;
  resume_filename: string;
  resume_note: string;
  error: string;
  // Always false. A literal field rather than an absence, so the UI has
  // something to assert on; nothing in the agent can set it true.
  submitted: boolean;
  browser_open: boolean;
  // Computed server-side so the UI never rewords them.
  headline: string;
  instruction: string;
  summary_line: string;
  counts: Record<string, number>;
  needs_you: number;
  total: number;
};

export function handoffGroup(report: HandoffReport, group: HandoffGroup): HandoffItem[] {
  return report.items.filter((i) => i.group === group);
}

// The badge for an item, falling back to the raw reason rather than to nothing:
// a future Python reason this file has not learned yet must still render as
// SOMETHING, or a field would silently lose the only text explaining its state.
export function handoffReasonTag(item: HandoffItem): string {
  return HANDOFF_REASON_TAG[item.reason] ?? item.reason;
}

// What the verify button came back with. `checked: false` is a don't-know, not a
// failure, and must never be shown as one — see the endpoint's docstring.
export type Verification = {
  checked: boolean;
  confirmed: boolean;
  reason: string;
};

// One sentence for each of the three outcomes. Only `confirmed` is good news;
// neither of the other two is bad news, and the wording must not let them read
// that way — "not verified" is a don't-know, and the row is logged in all three
// cases. The tracker renders the same distinction per row (ApplicationRow's
// ConfirmedMark), so this is the modal saying the same thing at the moment it
// happens.
export function verificationLine(v: Verification): string {
  if (v.confirmed) {
    return "✓ Verified — an ATS confirmation page was read for this application.";
  }
  if (v.checked) {
    return `Logged, but not verified: ${v.reason} That is not a failure — it only means nothing could vouch for the submission, so the tracker will show it as unverified.`;
  }
  return `Logged. The form window could not be re-read (${v.reason}), so this application stays unverified.`;
}
