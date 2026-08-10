# Applier answer rulings — Kayla, 2026-08-05

Taken from the handoff report of a **real** assisted-apply run against
`Cloudflare:greenhouse:8105728` ("U.S. Public Policy and AI Innovation Intern (Fall 2026)") — the
first live run of the feature. Eight questions came back unanswered; these rulings cover six of them.

Every one of these still lands in `BLOCKING_KINDS` or is otherwise **surfaced for Kayla to confirm**.
Nothing here changes THE ONE RULE, and nothing here is auto-submitted.

---

## 1. Canadian work authorization — a co-op work permit

Kayla is an international co-op student at Waterloo holding a **co-op work permit**: authorized to
work in Canada, no sponsorship needed there. Her US position is different — she needs sponsorship.

`profile_store.WORK_AUTH` offered six values shared across both countries, two of which
(`f1_opt`, `tn_eligible`) are **US immigration categories**. `ca_work_auth` was set to `tn_eligible`,
which produced the note *"Eligible for TN status under USMCA"* on Canadian forms — TN is about
working in the **US**.

**Ruling:** add a `coop_permit` value. Maps to authorized-in-Canada = Yes, sponsorship = No.

**Not done, deliberately:** `_ELIG_DOCUMENT` questions ("do you hold a valid work permit?") stay
non-mappable. A permit holder could honestly answer yes, but widening that gate is scope creep, and
the existing comment explains why the split exists.

## 2. Phone / address country

Blanked because country is not a typed profile field and the resolver has no `country` kind. It is
**not** because the control is a dropdown — the applier fills selects fine.

**Ruling:** derive it from `location`, now set to `"Waterloo, ON, Canada"`.

## 3. "How did you hear about this job?"

**Ruling:** answer with the company-website / careers-site option when one is offered. This is true
by construction: `agents/job_scraper` fetches from official ATS boards, so that *is* where it was
found. Where no such option exists, leave blank.

## 4. "Are you currently enrolled … and will return to the program upon completion?"

True for Kayla: a co-op student returns to school after each work term.

**Ruling:** answer Yes — but keyed on `grad_date` being **in the future**, not hardcoded. A blanket
yes becomes a lie the day she graduates; this version stops answering by itself.

## 5. Location questions — SPLIT BY PHRASING

Kayla's initial instruction was "choose yes as long as it's inside the US and Canada". Declined as
stated, and she agreed: she lives in Waterloo, and *"Are you currently residing in the greater
Washington D.C. Area or have confirmed plans to be in Washington D.C. …"* asks where she physically
is or will be. Answering yes because Canada is in her target set is a factual misrepresentation on a
real application — the same defect class as the four wrong-value bugs Phase B found.

**Ruling:** split on what the question actually asks.

| phrasing | answer |
|---|---|
| "willing to relocate to X", "able to work from X", "open to working in X" | **Yes** when X is in the US/Canada — a willingness statement, and hers is genuinely yes |
| "currently residing in X", "have confirmed plans to be in X", "are you located in X" | **blank** — a claim about her actual location |

## 6. Sponsorship — fix the cause, not the symptom

Kayla's initial instruction was "always fill in yes". Declined as stated, and she agreed.

The question blanked with *"does not name a single country"* — but the real cause is that the
**posting's `country` is `UNKNOWN`**, on a posting whose title begins "U.S.". `_resolve_eligibility`
already accepts a `default_country` from the posting row and would have answered from `us_work_auth`
had the row been classified. **40 of 580 rows are `UNKNOWN`.**

**Ruling:** fix the country classification and backfill the UNKNOWN rows. Then the existing rule
answers **Yes** on US roles and **No** on Canadian ones — correct per country. A blanket "always yes"
would be wrong on every Canadian posting, where the co-op permit means no sponsorship is needed.

---

## Not covered by these rulings

Two questions from that run remain hers alone, correctly:

- **The Cloudflare candidate-privacy acknowledgement** — a consent. `consent` is in `BLOCKING_KINDS`
  and must stay there.
- **"Resume/CV — the résumé did not land: the field reads 'nothing' instead of
  `master__2026-07-24T21_30_36.pdf`"** — this is a **bug report from a live run**, not a ruling. The
  attach path read back an empty filename. Investigate separately; it is the one item in that report
  describing something that did not work as designed.
