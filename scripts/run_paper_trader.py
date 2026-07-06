"""Run the paper-trader agent once.

  uv run python scripts/run_paper_trader.py            # dry-run: decide, print, NO orders
  uv run python scripts/run_paper_trader.py --send      # place PAPER orders + deliver to Discord

Paper money only (Alpaca paper-api). Requires ALPACA_API_KEY_ID / _SECRET_KEY in
.env to actually place orders; without them it runs dry. Create data/TRADING_HALTED
to hard-stop all order placement.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.paper_trader.graph import build_paper_trader_graph


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the paper trader.")
    parser.add_argument("--send", action="store_true", help="place paper orders + deliver")
    args = parser.parse_args()

    graph = build_paper_trader_graph(send=args.send)
    final = graph.invoke({})

    print("=" * 60)
    print(final.get("message", "(no message produced)"))
    print("=" * 60)


if __name__ == "__main__":
    main()
