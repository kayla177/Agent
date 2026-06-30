"""Run the job-scraper agent once.

  uv run python scripts/run_job_scraper.py           # build + print only (no Discord)
  uv run python scripts/run_job_scraper.py --send     # also deliver to Discord

This is both the dev entrypoint and what the launchd job calls (with --send).
Note: even in print-only mode the seen-store IS updated, so a second run will
report no new roles (this is the intended dedupe behavior).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a plain script (e.g. from launchd) by putting the project
# root on the path so the `agents` / `shell` packages import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.job_scraper.graph import build_job_scraper_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the job scraper.")
    parser.add_argument("--send", action="store_true", help="deliver to Discord")
    args = parser.parse_args()

    graph = build_job_scraper_graph(send=args.send)
    final = graph.invoke({})

    print("=" * 60)
    print(final.get("message", "(no message produced)"))
    print("=" * 60)
    if args.send:
        print("Delivery attempted (see above for any warnings).")


if __name__ == "__main__":
    main()
