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

const { bestMatch, isScreenedOut, eligibleLabel, inCountries } = await import(
  "../src/lib/jobs.ts"
);

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

if (process.exitCode) {
  console.error("\n✗ jobs board filter checks FAILED");
} else {
  console.log(`✓ ${passed} jobs board filter checks passed`);
}
