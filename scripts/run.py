"""Run any registered agent once — the dev entrypoint and what launchd calls.

  python scripts/run.py <agent_key>          # build + print only (no Discord)
  python scripts/run.py <agent_key> --send   # also deliver to Discord

Agent keys come from agents/registry.py (morning_briefing, stock_digest,
job_scraper, application_tracker, gmail_sync). The resume generator has its own
richer CLI (experience-pool management + per-job drafting): scripts/run_resume_generator.py.

Replaces the former five near-identical run_<agent>.py wrappers — one generic
driver over the registry's build_graph(send=...) contract.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

# Allow running as a plain script (e.g. from launchd) by putting the project
# root on the path so the `agents` / `shell` packages import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.registry import get_spec, list_specs
from server import db


def main() -> None:
    keys = [spec.key for spec in list_specs()]
    parser = argparse.ArgumentParser(description="Run a registered agent once.")
    parser.add_argument("agent", choices=keys, metavar="agent_key",
                        help=f"which agent to run ({', '.join(keys)})")
    parser.add_argument("--send", action="store_true", help="deliver to Discord")
    parser.add_argument(
        "--backfill", action="store_true",
        help="also LLM-refine stored postings that lack a real fit score "
             "(job_scraper only; ~6.5s per posting, so the scheduled runs use "
             "it and interactive runs do not)",
    )
    args = parser.parse_args()

    # Record the run, the same way the FastAPI drivers do.
    #
    # This entry point is what launchd calls, and it used to record NOTHING: the
    # graph was invoked directly, so no `runs` row existed and `/history` showed
    # only the runs started by hand from the dashboard. That is how a scrape with
    # 261 fetch failures went unnoticed for days — `jobscraper.log` was the only
    # place it appeared at all — and it would have made the `NoBoardReachable`
    # guard invisible too, since there was no row for it to fail.
    #
    # Deliberately NOT the whole of `server/runner.py`: no SSE, no per-node
    # events. A scheduled run has no subscriber to stream to, and the row plus
    # its status and message is what was actually missing.
    run_id = db.create_run(args.agent, args.send)
    try:
        graph = get_spec(args.agent).build_graph(send=args.send)
        final = graph.invoke({"backfill": True} if args.backfill else {})
    except BaseException as exc:  # noqa: BLE001 — record it, then re-raise
        # Re-raised, so launchd still sees a non-zero exit and the traceback
        # still reaches jobscraper.error.log. The row is an addition, not a
        # replacement for the log.
        db.finish_run(run_id, "error", None, traceback.format_exc())
        print(f"! {args.agent} failed: {exc}", file=sys.stderr)
        raise

    message = final.get("message", "") or ""
    state_error = final.get("error")
    db.finish_run(
        run_id,
        "error" if state_error else "success",
        message,
        str(state_error) if state_error else None,
    )

    print("=" * 60)
    print(message or "(no message produced)")
    print("=" * 60)
    if state_error:
        print(f"! finished with error: {state_error}", file=sys.stderr)
    if args.send:
        print("Delivery attempted (see above for any warnings).")


if __name__ == "__main__":
    main()
