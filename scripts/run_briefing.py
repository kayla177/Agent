"""Run the morning-briefing agent once.

  uv run python scripts/run_briefing.py           # build + print only (no Discord)
  uv run python scripts/run_briefing.py --send     # also deliver to Discord

This is both the dev entrypoint and what the launchd job calls (with --send).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a plain script (e.g. from launchd) by putting the project
# root on the path so the `agents` / `shell` packages import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.morning_briefing.graph import build_briefing_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the morning briefing.")
    parser.add_argument("--send", action="store_true", help="deliver to Discord")
    args = parser.parse_args()

    graph = build_briefing_graph(send=args.send)
    final = graph.invoke({})

    print("=" * 60)
    print(final.get("message", "(no message produced)"))
    print("=" * 60)
    if args.send:
        print("Delivery attempted (see above for any warnings).")


if __name__ == "__main__":
    main()
