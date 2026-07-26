"""Offline tests for the gmail-sync agent (no network, no pytest).

Run:  .venv/bin/python -m pytest tests/test_gmail_sync.py

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
from agents.gmail_sync.nodes import scan_gmail as gnode



def test_matching() -> None:
    assert matching.normalize_company("Stripe, Inc.") == "stripe", "normalize strips suffix+punct"
    assert matching.company_matches("Stripe", "Re: your application to Stripe"), "company match in subject"
    assert matching.company_matches("Notion", "Notion Recruiting <x@greenhouse.io>"), "company match via sender name"
    assert matching.company_matches("Figma", "Weekly newsletter from Medium") is False, "no match unrelated"
    assert matching.company_matches("AB", "AB Corp interview") is False, "short key guarded"
    assert matching.infer_status("Unfortunately we decided not to move forward") == "rejected", "infer rejected"
    assert matching.infer_status("We are pleased to offer you the role") == "offer", "infer offer"
    assert matching.infer_status("Let's schedule a call for next steps") == "interview", "infer interview"
    assert matching.infer_status("Your weekly digest") is None, "infer none"
    assert matching.rank_status("rejected") > matching.rank_status("offer") > matching.rank_status("interview"), "rank rejected>offer>interview"
    assert matching.should_apply("applied", "interview") is True, "apply advances"
    assert matching.should_apply("offer", "interview") is False, "apply no regress"
    assert matching.should_apply("offer", "rejected") is True, "apply rejection always"
    assert matching.should_apply("rejected", "rejected") is False, "apply rejection idempotent"
    assert matching.should_apply("rejected", "offer") is False, "apply never revives rejected (offer)"
    assert matching.should_apply("rejected", "interview") is False, "apply never revives rejected (interview)"


def test_node_with_fake_gmail() -> None:
    store_db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    store_db.init_db()
    appstore.add_application("Stripe", "SWE Intern", status="applied")   # id 1
    appstore.add_application("Figma", "FE Intern", status="interview")   # id 2
    appstore.add_application("Notion", "PM Intern", status="applied")    # id 3
    appstore.add_application("Databricks", "DE Intern", status="rejected")  # id 4

    fake_emails = [
        {"from_name": "Stripe Recruiting", "from_email": "jobs@greenhouse.io",
         "subject": "Next steps for your Stripe application", "snippet": "Let's schedule a call."},
        {"from_name": "Figma Talent", "from_email": "no-reply@figma.com",
         "subject": "Figma — update", "snippet": "Unfortunately we won't be moving forward."},
        # Notion: no email → unchanged
        {"from_name": "Databricks Recruiting", "from_email": "x@databricks.com",
         "subject": "Databricks — next steps", "snippet": "Let's schedule a call."},
    ]
    gnode.fetch_job_emails = lambda **_: fake_emails  # monkeypatch the name in node's namespace

    result = gnode.scan_gmail_node({})
    apps = {a["company"]: a for a in appstore.load_all()}
    assert apps["Stripe"]["status"] == "interview", "Stripe advanced applied→interview"
    assert apps["Stripe"]["auto_detected"] is True, "Stripe flagged auto_detected"
    assert apps["Figma"]["status"] == "rejected", "Figma interview→rejected"
    assert apps["Notion"]["status"] == "applied", "Notion unchanged (no email)"
    assert apps["Notion"]["auto_detected"] is False, "Notion not auto_detected"
    assert apps["Databricks"]["status"] == "rejected", "rejected app not revived by matching email"
    assert "updated 2" in result["message"], "message mentions 2 updates"


def test_node_not_authorized() -> None:
    store_db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    store_db.init_db()
    appstore.add_application("Stripe", "SWE Intern", status="applied")
    gnode.fetch_job_emails = lambda **_: None  # simulate no-auth
    result = gnode.scan_gmail_node({})
    assert "not authorized" in result["message"].lower(), "returns auth hint"
    assert appstore.load_all()[0]["status"] == "applied", "no status change"


def test_node_exception() -> None:
    store_db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    store_db.init_db()
    appstore.add_application("Stripe", "SWE Intern", status="applied")

    def _boom(**_):
        raise RuntimeError("gmail down")

    gnode.fetch_job_emails = _boom
    result = gnode.scan_gmail_node({})
    assert "failed" in result["message"].lower(), "returns failure message"
    assert appstore.load_all()[0]["status"] == "applied", "no status change on error"
