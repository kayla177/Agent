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
from pathlib import Path

# Allow running as a plain script (e.g. from launchd) by putting the project
# root on the path so the `agents` / `shell` packages import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.registry import get_spec, list_specs


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

    graph = get_spec(args.agent).build_graph(send=args.send)
    final = graph.invoke({"backfill": True} if args.backfill else {})

    print("=" * 60)
    print(final.get("message", "(no message produced)"))
    print("=" * 60)
    if args.send:
        print("Delivery attempted (see above for any warnings).")


if __name__ == "__main__":
    main()
