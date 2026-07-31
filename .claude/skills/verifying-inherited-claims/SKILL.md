---
name: verifying-inherited-claims
description: Use when a number, timing, benchmark, limit, capacity, or capability claim arrives from context — a plan, an earlier message, a docstring, a code comment, or your own earlier measurement — and your output will multiply it, budget from it, schedule around it, size something to it, or recommend based on it.
---

# Verifying Inherited Claims

## Overview

A number that arrives in your context is a **hypothesis**, not a measurement. The moment you multiply it, budget from it, or recommend on it, you have adopted it as your own claim — and your confident prose will make it sound measured even though nobody checked.

**If a claim drives your output and re-measuring is cheap, re-measure it.**

## The Trigger

Both conditions true:

1. A quantitative or capability claim reached you from context rather than from your own observation in this session.
2. Your output multiplies it, sizes to it, schedules around it, or recommends from it.

If both hold, measure before you use it. One command is usually enough.

## What Cheap Means

Cheap is the common case, not the exception:

| Claim | Cheap check |
|---|---|
| "~6.5s per item" | Time one real batch |
| "context window is 2048" | Ask the server; or send a longer prompt with a needle at the far end |
| "the model can't do X" | Try X once |
| "descriptions are ~5k chars" | `SELECT length(...)` — check p50 **and** max |
| "this table has ~500 rows" | `SELECT COUNT(*)` |
| "the file is unchanged" | Query through the layer that reads it, not the filesystem |

If measuring genuinely costs more than being wrong, say which number you inherited, from where, and what changes if it's wrong. That is a different output from silently adopting it.

## Claims Travel in Clusters

A sentence carrying one unverified number usually carries others. Check every load-bearing figure in the same breath, not just the one that caught your eye.

In testing, a prompt planted one wrong number ("6.5 s per posting"). The agent that re-measured it also found two more in the same two sentences: the stated backlog of 158 was really 105 (dismissed rows are excluded), and a hardcoded cap meant any single run touched at most 60. Verifying one number surfaced three.

## Recipe

State the inherited claim and its source. Measure it. Report measured-versus-inherited. Then use the measured value.

```
Inherited: ~6.5 s/posting (from an earlier profiling note)
Measured:  0.80–3.14 s/posting over 3 real batches, warm model
Using:     ~1.5 s/posting → 158 postings ≈ 4 min, not 17
```

## Red Flags

You are about to inherit a claim unverified:

- You are doing arithmetic on a number you did not produce
- You are writing "should take about…", "roughly N minutes", "fits comfortably in…"
- You catch yourself explaining *why* the inherited number is plausible
- The claim appears in a docstring, comment, or plan and you are treating that as evidence
- The number came from **your own** earlier measurement, under different conditions
- You are re-stating a figure you have already told someone more than once

## Rationalizations

| Excuse | Reality |
|---|---|
| "It was profiled earlier" | Under what conditions? Cold model, different batch size, different data. Re-measure. |
| "It's in the docstring" | Docstrings record what someone believed once. They are not instruments. |
| "I measured it myself earlier" | Same trap, extra confidence. Conditions change; so does the number. |
| "It's only an estimate for the user" | An estimate you present as fact is a claim. Off-by-4× wrecks their scheduling decision. |
| "Measuring costs time" | One `time` call or one `COUNT(*)`. Cheaper than a wrong recommendation. |
| "The order of magnitude is what matters" | 4× errors change "do it now" into "leave it overnight". |
| "The plan says so, and the plan was reviewed" | Review reads intent. It does not execute anything. |

## Special Case: Filesystem Evidence

`mtime`, size, and checksums are **not valid evidence** that a datastore is unchanged when writes can land elsewhere — SQLite in WAL mode is the common example: writes go to `-wal` and the main file's timestamp stays frozen.

Verify through the layer that reads the data (a query), not the container.

## Real-World Impact

Measured in this codebase:

- An inherited "~6.5 s/posting" produced a confident "~17 minutes, run it now" recommendation, with invented supporting reasoning about the model being warm. Measured: 0.80–3.14 s/posting — **~4 minutes**.
- An inherited "context window is 2048" caused a prompt budget to be sized 16× too small, discarding ~20 points of information capture. The model reported 131072 and held a 10k-token prompt with a needle at position 0.
- A stored-description length was assumed adequate from reading the fetch code. Measured: every row was exactly 1200 chars because of a cap in that same code, so keyword matching hit 4% of postings instead of the expected majority.
- A subagent verified a database was untouched using file mtime. Under WAL that is meaningless; the database had in fact been written.

## When NOT to Use

- The claim does not affect your output (background colour, not load-bearing).
- You produced the measurement in this session, under the same conditions, and nothing since could have changed it.
- Measuring requires an action you are not authorized to take — then name the assumption explicitly instead of hiding it.
