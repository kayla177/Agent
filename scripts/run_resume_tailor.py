"""Tailor a résumé + cover letter from the command line.

  uv run python scripts/run_resume_tailor.py --jd-file jd.txt --company Stripe --role "SWE Intern"
  uv run python scripts/run_resume_tailor.py --company Stripe --role "SWE Intern" --send   # + Discord ping

Reads the base résumé from data/resume.md. The job description comes from
--jd-file, or from stdin if omitted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.resume_tailor.graph import build_resume_tailor_graph
from agents.resume_tailor.store import save_draft


def main() -> None:
    parser = argparse.ArgumentParser(description="Tailor a résumé to a job.")
    parser.add_argument("--jd-file", help="path to a text file with the job description")
    parser.add_argument("--company", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--send", action="store_true", help="ping Discord when done")
    parser.add_argument("--save", action="store_true", help="save a draft under data/drafts/")
    args = parser.parse_args()

    if args.jd_file:
        jd = Path(args.jd_file).read_text(encoding="utf-8")
    else:
        print("Paste the job description, then Ctrl-D:")
        jd = sys.stdin.read()

    graph = build_resume_tailor_graph(send=args.send)
    out = graph.invoke(
        {"job_description": jd, "company": args.company, "role": args.role}
    )

    message = out.get("message", "(no output)")
    print("=" * 60)
    print(message)
    print("=" * 60)
    if args.save and not out.get("error"):
        path = save_draft(args.company or "company", args.role or "role", message)
        print(f"saved draft -> {path}")


if __name__ == "__main__":
    main()
