"""Run the resume generator, or manage the experience pool, from the CLI.

  # Add your real background to the pool (repeatable; PDF / DOCX / TXT / MD):
  uv run python scripts/run_resume_generator.py --add-experience resume.pdf
  uv run python scripts/run_resume_generator.py --add-experience proj.docx --kind project

  # List what's in the pool:
  uv run python scripts/run_resume_generator.py --list

  # Draft a tailored resume for one scraped job (needs Ollama running):
  uv run python scripts/run_resume_generator.py --job-id "<job id from the jobs store>"

This is the dev/testing entrypoint until the Next.js Resume tab lands; the same
store + graph will back the web upload button and the per-job "Generate resume"
action.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a plain script by putting the project root on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.resume_generator import store as resume_store
from agents.resume_generator.graph import build_resume_generator_graph
from agents.resume_generator.parse_upload import extract_text


def _add_experience(path: str, kind: str) -> None:
    text = extract_text(path)
    if not text.strip():
        print(f"! No text extracted from {path} (nothing added).")
        return
    doc_id = resume_store.add_experience_doc(Path(path).name, text, kind=kind)
    print(f"Added {kind} doc #{doc_id} from {path} ({len(text)} chars).")


def _list() -> None:
    docs = resume_store.list_experience_docs()
    if not docs:
        print("Experience pool is empty. Add with --add-experience <file>.")
        return
    print(f"Experience pool ({len(docs)} doc(s)):")
    for d in docs:
        print(f"  #{d['id']:>3}  [{d['kind']:<7}] {d['filename']}  "
              f"({d['chars']} chars, added {d['added_at']})")


def _generate(job_id: str) -> None:
    graph = build_resume_generator_graph(send=False)
    final = graph.invoke({"job_id": job_id})

    print("=" * 60)
    print(final.get("message", "(no message produced)"))
    print("=" * 60)
    markdown = final.get("markdown")
    if markdown:
        print(markdown)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume generator / experience pool.")
    parser.add_argument("--add-experience", metavar="PATH",
                        help="parse a resume/project file into the experience pool")
    parser.add_argument("--kind", choices=resume_store.KINDS, default="resume",
                        help="kind of doc being added (default: resume)")
    parser.add_argument("--list", action="store_true",
                        help="list the experience pool and exit")
    parser.add_argument("--job-id", metavar="ID",
                        help="draft a tailored resume for this scraped job id")
    args = parser.parse_args()

    if args.add_experience:
        _add_experience(args.add_experience, args.kind)
    if args.list:
        _list()
    if args.job_id:
        _generate(args.job_id)
    if not (args.add_experience or args.list or args.job_id):
        parser.print_help()


if __name__ == "__main__":
    main()
