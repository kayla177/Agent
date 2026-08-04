#!/usr/bin/env node
// Board-filter guard for the undergrad-eligibility screen.
//
// `rank_node` used to DROP an ineligible posting before it was ever persisted.
// It now tags the row instead, and the BOARD is what hides it — so the hiding
// logic here is load-bearing in a way it wasn't before: get it wrong in one
// direction and a role the user could actually apply to is invisible with no way
// to find it; get it wrong in the other and the hero advertises a posting that
// isn't in the list beneath it.
//
// There is no JS test framework in this project (and adding one is out of scope),
// so this follows the plain-node precedent of check-schema-drift.mjs. The
// functions under test are pure, which is exactly why they live in lib/jobs.ts.
//
// Run: npm run check:jobs
import assert from "node:assert/strict";

const {
  bestMatch, isScreenedOut, eligibleLabel, inCountries,
  AUTOFILL_ATS, supportsAutofill, autofillUnavailableNote,
  HANDOFF_GROUPS, handoffGroup, handoffReasonTag, verificationLine,
} = await import("../src/lib/jobs.ts");

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed += 1;
  } catch (err) {
    console.error(`✗ ${name}\n  ${err.message}`);
    process.exitCode = 1;
  }
}

const job = (over = {}) => ({
  id: "j", company: "Acme", title: "SWE Intern", location: "Austin, TX", url: "",
  status: "new", ats: "greenhouse", posted_at: new Date().toISOString().slice(0, 10),
  remote: null, compensation: null, department: null, description: null,
  fit_score: 50, fit_reason: "", ghost: 0, ghost_reason: "", also_on: "[]",
  country: "US", eligible: 1, eligible_reason: "", ...over,
});

// The board's default list filter, mirroring JobsBoard's `list` memo.
const boardList = (jobs, { showScreenedOut = false } = {}) =>
  jobs
    .filter((j) => j.status !== "dismissed")
    .filter((j) => (showScreenedOut ? true : !isScreenedOut(j)))
    .filter((j) => inCountries(j, ["US", "CA", "UNKNOWN"]));

test("a screened-out row is hidden from the default board list", () => {
  const rows = [job({ id: "ok" }), job({ id: "no", eligible: 0, eligible_reason: "requires PhD" })];
  assert.deepEqual(boardList(rows).map((j) => j.id), ["ok"]);
});

test("a screened-out row IS present when revealed", () => {
  const rows = [job({ id: "ok" }), job({ id: "no", eligible: 0 })];
  assert.deepEqual(
    boardList(rows, { showScreenedOut: true }).map((j) => j.id).sort(),
    ["no", "ok"],
  );
});

test("bestMatch never surfaces a screened-out row, even as the top scorer", () => {
  // The hero cannot advertise something hidden from the list underneath it.
  const rows = [
    job({ id: "hidden", fit_score: 99, eligible: 0, eligible_reason: "requires PhD" }),
    job({ id: "shown", fit_score: 60 }),
  ];
  assert.equal(bestMatch(rows).id, "shown");
});

test("bestMatch returns null when every candidate is screened out", () => {
  const rows = [job({ id: "a", fit_score: 99, eligible: 0 }), job({ id: "b", fit_score: 90, eligible: 0 })];
  assert.equal(bestMatch(rows), null, "better no hero than a hidden one");
});

test("bestMatch is unchanged when nothing is screened out", () => {
  const rows = [job({ id: "a", fit_score: 70 }), job({ id: "b", fit_score: 95 })];
  assert.equal(bestMatch(rows).id, "b");
});

test("isScreenedOut defaults to visible for anything but an explicit 0", () => {
  assert.equal(isScreenedOut(job({ eligible: 1 })), false);
  assert.equal(isScreenedOut(job({ eligible: 0 })), true);
  // Default-open: only an explicit judgement hides a row.
  assert.equal(isScreenedOut(job({ eligible: undefined })), false);
  assert.equal(isScreenedOut(job({ eligible: null })), false);
});

test("eligibleLabel always renders something", () => {
  assert.equal(eligibleLabel(job({ eligible_reason: "requires PhD" })), "requires PhD");
  assert.equal(eligibleLabel(job({ eligible_reason: "" })), "not undergrad-eligible");
  assert.equal(eligibleLabel(job({ eligible_reason: "   " })), "not undergrad-eligible");
});

// ---------------------------------------------------------------------------
// Assisted apply — which boards get the button, and how the handoff is read
// ---------------------------------------------------------------------------
// The Python side pins the LISTS against the agent's own
// (tests/test_applier_ui.py). What is checked here is the BEHAVIOUR those lists
// drive, which is what the modal actually branches on.

test("autofill is offered for exactly the boards the agent can read", () => {
  for (const ats of AUTOFILL_ATS) assert.equal(supportsAutofill(ats), true, ats);
  for (const ats of ["workday", "smartrecruiters", "workable"]) {
    assert.equal(supportsAutofill(ats), false, ats);
  }
});

test("an absent or oddly-cased ATS is decided, never guessed", () => {
  // The `jobs` row is the source of truth and it is not always tidy; a blank
  // board must fall to the manual path rather than to a button that errors.
  assert.equal(supportsAutofill(""), false);
  assert.equal(supportsAutofill(null), false);
  assert.equal(supportsAutofill(undefined), false);
  assert.equal(supportsAutofill("Greenhouse"), true);
  assert.equal(supportsAutofill("  lever  "), true);
});

test("the unavailable note names the board and says what to do instead", () => {
  const note = autofillUnavailableNote("workday");
  assert.ok(note.includes("workday"), note);
  assert.ok(note.includes("fill it in yourself"), note);
  // An unknown board still gets a sentence rather than a dangling one.
  assert.ok(autofillUnavailableNote("").includes("unknown"));
});

const item = (over = {}) => ({
  key: "k", label: "Full name", group: "done", reason: "filled", section: "",
  required: false, status: "filled", value: "x", intended: "x", suggestion: "",
  note: "", drafted: false, kind: "text", ...over,
});

const report = (items) => ({
  items, job_title: "", company: "", form_url: "", resume_filename: "",
  resume_note: "", error: "", submitted: false, browser_open: true,
  headline: "", instruction: "", summary_line: "", counts: {}, needs_you: 0,
  total: items.length,
});

test("the blocking band is read first and the filled band last", () => {
  // The order is the whole point: the blocking band is the only one that costs
  // you the application if you miss it.
  assert.equal(HANDOFF_GROUPS[0], "blocking");
  assert.equal(HANDOFF_GROUPS[HANDOFF_GROUPS.length - 1], "done");
});

test("items are bucketed by their own group and nothing is dropped", () => {
  const rows = [
    item({ key: "a", group: "blocking", reason: "empty" }),
    item({ key: "b", group: "review", reason: "drafted" }),
    item({ key: "c", group: "withheld", reason: "withheld_eeo" }),
    item({ key: "d", group: "done" }),
  ];
  const r = report(rows);
  const seen = HANDOFF_GROUPS.flatMap((g) => handoffGroup(r, g)).map((i) => i.key);
  assert.deepEqual(seen, ["a", "b", "c", "d"], "every item lands in exactly one band");
});

test("an unknown reason still renders as something", () => {
  // A field that lost the only text explaining its state is worse than an ugly
  // one, so the raw reason is the fallback.
  assert.equal(handoffReasonTag(item({ reason: "drafted" })), "AI-DRAFTED — read every word");
  assert.equal(handoffReasonTag(item({ reason: "some_future_reason" })), "some_future_reason");
});

test("verification wording never claims an application was not submitted", () => {
  const confirmed = verificationLine({ checked: true, confirmed: true, reason: "" });
  assert.ok(confirmed.includes("Verified"));

  const unverified = verificationLine({ checked: true, confirmed: false, reason: "no wording." });
  assert.ok(unverified.includes("not verified"), unverified);
  assert.ok(unverified.includes("not a failure"), unverified);
  assert.ok(!unverified.includes("not submitted"), unverified);

  const unread = verificationLine({ checked: false, confirmed: false, reason: "no window." });
  assert.ok(unread.includes("Logged"), unread);
  assert.ok(unread.includes("unverified"), unread);
  assert.ok(!unread.includes("not submitted"), unread);
});

if (process.exitCode) {
  console.error("\n✗ jobs board filter checks FAILED");
} else {
  console.log(`✓ ${passed} jobs board filter checks passed`);
}
