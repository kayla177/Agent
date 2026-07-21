"""Offline tests for the gmail-sync agent (no network, no pytest).

Run:  uv run python tests/test_gmail_sync.py

Covers the pure matching/inference heuristics and the node's behavior against a
canned email list + a temp SQLite store (fetch_job_emails is monkeypatched).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store_db
from agents.application_tracker import store as appstore
from agents.gmail_sync import matching
from agents.gmail_sync import node as gnode

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _failures.append(name)


def test_matching() -> None:
    print("matching heuristics")
    check("normalize strips suffix+punct", matching.normalize_company("Stripe, Inc.") == "stripe")
    check("company match in subject", matching.company_matches("Stripe", "Re: your application to Stripe"))
    check("company match via sender name", matching.company_matches("Notion", "Notion Recruiting <x@greenhouse.io>"))
    check("no match unrelated", matching.company_matches("Figma", "Weekly newsletter from Medium") is False)
    check("short key guarded", matching.company_matches("AB", "AB Corp interview") is False)
    check("infer rejected", matching.infer_status("Unfortunately we decided not to move forward") == "rejected")
    check("infer offer", matching.infer_status("We are pleased to offer you the role") == "offer")
    check("infer interview", matching.infer_status("Let's schedule a call for next steps") == "interview")
    check("infer none", matching.infer_status("Your weekly digest") is None)
    check("rank rejected>offer>interview", matching.rank_status("rejected") > matching.rank_status("offer") > matching.rank_status("interview"))
    check("apply advances", matching.should_apply("applied", "interview") is True)
    check("apply no regress", matching.should_apply("offer", "interview") is False)
    check("apply rejection always", matching.should_apply("offer", "rejected") is True)
    check("apply rejection idempotent", matching.should_apply("rejected", "rejected") is False)


def test_node_with_fake_gmail() -> None:
    print("scan_gmail node (monkeypatched fetch + temp DB)")
    store_db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    store_db.init_db()
    appstore.add_application("Stripe", "SWE Intern", status="applied")   # id 1
    appstore.add_application("Figma", "FE Intern", status="interview")   # id 2
    appstore.add_application("Notion", "PM Intern", status="applied")    # id 3

    fake_emails = [
        {"from_name": "Stripe Recruiting", "from_email": "jobs@greenhouse.io",
         "subject": "Next steps for your Stripe application", "snippet": "Let's schedule a call."},
        {"from_name": "Figma Talent", "from_email": "no-reply@figma.com",
         "subject": "Figma — update", "snippet": "Unfortunately we won't be moving forward."},
        # Notion: no email → unchanged
    ]
    gnode.fetch_job_emails = lambda **_: fake_emails  # monkeypatch the name in node's namespace

    result = gnode.scan_gmail_node({})
    apps = {a["company"]: a for a in appstore.load_all()}
    check("Stripe advanced applied→interview", apps["Stripe"]["status"] == "interview")
    check("Stripe flagged auto_detected", apps["Stripe"]["auto_detected"] is True)
    check("Figma interview→rejected", apps["Figma"]["status"] == "rejected")
    check("Notion unchanged (no email)", apps["Notion"]["status"] == "applied")
    check("Notion not auto_detected", apps["Notion"]["auto_detected"] is False)
    check("message mentions 2 updates", "updated 2" in result["message"])


def test_node_not_authorized() -> None:
    print("scan_gmail node (not authorized → graceful)")
    store_db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    store_db.init_db()
    appstore.add_application("Stripe", "SWE Intern", status="applied")
    gnode.fetch_job_emails = lambda **_: None  # simulate no-auth
    result = gnode.scan_gmail_node({})
    check("returns auth hint", "not authorized" in result["message"].lower())
    check("no status change", appstore.load_all()[0]["status"] == "applied")


def main() -> int:
    for fn in (test_matching, test_node_with_fake_gmail, test_node_not_authorized):
        fn()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("all gmail-sync tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
