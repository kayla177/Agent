"""Jobs router tests.

The behavior under test is the fix for the worst bug in the tab: Apply used to
create an application and mark the job applied WITHOUT opening the posting and
WITHOUT recording which résumé was used, so the tracker asserted things that had
never happened.
"""

from __future__ import annotations

from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore

_JOB = {
    "id": "Acme:greenhouse:1", "company": "Acme", "title": "SWE Intern",
    "location": "Austin, TX", "url": "https://acme.example/jobs/1",
    "status": "new", "country": "US", "fit_score": 80, "fit_reason": "matched: Python",
}


def test_apply_records_resume_link(client):
    jobstore.replace_record(dict(_JOB))
    res = client.post("/data/jobs/apply", json={"id": _JOB["id"], "resume_job_id": "some:job:1"})
    assert res.status_code == 200
    app_id = res.json()["application_id"]

    apps = appstore.load_all()
    assert len(apps) == 1
    assert apps[0]["id"] == app_id
    assert apps[0]["resume_job_id"] == "some:job:1"
    assert apps[0]["company"] == "Acme"
    assert jobstore.load_records()[_JOB["id"]]["status"] == "applied"


def test_apply_without_resume_is_allowed(client):
    jobstore.replace_record(dict(_JOB))
    res = client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    assert res.status_code == 200
    assert appstore.load_all()[0]["resume_job_id"] is None


def test_apply_unknown_job_404(client):
    assert client.post("/data/jobs/apply", json={"id": "nope"}).status_code == 404


def test_undo_apply_removes_row_and_reverts_status(client):
    jobstore.replace_record(dict(_JOB))
    app_id = client.post("/data/jobs/apply", json={"id": _JOB["id"]}).json()["application_id"]

    res = client.post("/data/jobs/undo-apply", json={"id": _JOB["id"], "application_id": app_id})
    assert res.status_code == 200
    assert appstore.load_all() == []
    # `viewed`, not `new`: reaching the apply modal means the posting was read.
    assert jobstore.load_records()[_JOB["id"]]["status"] == "viewed"


def test_status_endpoint_sets_viewed_and_restores_dismissed(client):
    jobstore.replace_record(dict(_JOB))
    assert client.post("/data/jobs/status", json={"id": _JOB["id"], "status": "viewed"}).status_code == 200
    assert jobstore.load_records()[_JOB["id"]]["status"] == "viewed"

    client.post("/data/jobs/dismiss", json={"id": _JOB["id"]})
    assert jobstore.load_records()[_JOB["id"]]["status"] == "dismissed"
    client.post("/data/jobs/status", json={"id": _JOB["id"], "status": "new"})
    assert jobstore.load_records()[_JOB["id"]]["status"] == "new"


def test_status_endpoint_rejects_bad_status(client):
    jobstore.replace_record(dict(_JOB))
    res = client.post("/data/jobs/status", json={"id": _JOB["id"], "status": "banana"})
    assert res.status_code == 400


def test_job_id_with_colons_is_not_mangled(client):
    """jobs.id contains ':' — ids travel in the BODY, never a path segment."""
    jobstore.replace_record(dict(_JOB))
    res = client.post("/data/jobs/status", json={"id": "Acme:greenhouse:1", "status": "viewed"})
    assert res.status_code == 200
