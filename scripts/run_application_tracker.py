"""Run the application-tracker agent once.

  uv run python scripts/run_application_tracker.py           # print only
  uv run python scripts/run_application_tracker.py --send     # also deliver to Discord
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.application_tracker.graph import build_tracker_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the application tracker.")
    parser.add_argument("--send", action="store_true", help="deliver to Discord")
    args = parser.parse_args()

    graph = build_tracker_graph(send=args.send)
    final = graph.invoke({})

    print("=" * 60)
    print(final.get("message", "(no message produced)"))
    print("=" * 60)
    if args.send:
        print("Delivery attempted (see above for any warnings).")


if __name__ == "__main__":
    main()
