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

_JOB_B = {
    "id": "Beta:greenhouse:2", "company": "Beta", "title": "Backend Intern",
    "location": "Remote", "url": "https://beta.example/jobs/2",
    "status": "new", "country": "US", "fit_score": 70, "fit_reason": "matched: Go",
}

# Two DISTINCT postings sharing both company AND title (same company advertising
# the same role through two different ATS boards) — the exact case a
# company/role heuristic cannot disambiguate.
_JOB_TWIN_1 = {
    "id": "Acme:site1:99", "company": "Acme", "title": "SWE Intern",
    "location": "Austin, TX", "url": "https://acme.example/site1/99",
    "status": "new", "country": "US", "fit_score": 80, "fit_reason": "matched: Python",
}
_JOB_TWIN_2 = {
    "id": "Acme:site2:99", "company": "Acme", "title": "SWE Intern",
    "location": "Austin, TX", "url": "https://acme.example/site2/99",
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


def test_undo_apply_rejects_application_from_different_job(client):
    """An application_id that belongs to a DIFFERENT job must be rejected —
    not deleted, and neither job's status touched."""
    jobstore.replace_record(dict(_JOB))
    jobstore.replace_record(dict(_JOB_B))
    app_a = client.post("/data/jobs/apply", json={"id": _JOB["id"]}).json()["application_id"]
    app_b = client.post("/data/jobs/apply", json={"id": _JOB_B["id"]}).json()["application_id"]

    # Try to undo job A's apply using job B's application id.
    res = client.post("/data/jobs/undo-apply", json={"id": _JOB["id"], "application_id": app_b})
    assert res.status_code == 409

    apps = {a["id"]: a for a in appstore.load_all()}
    assert app_a in apps
    assert app_b in apps  # B's application was NOT deleted
    assert jobstore.load_records()[_JOB["id"]]["status"] == "applied"
    assert jobstore.load_records()[_JOB_B["id"]]["status"] == "applied"


def test_undo_apply_nonexistent_application_404(client):
    jobstore.replace_record(dict(_JOB))
    client.post("/data/jobs/apply", json={"id": _JOB["id"]})

    res = client.post("/data/jobs/undo-apply", json={"id": _JOB["id"], "application_id": 999999})
    assert res.status_code == 404
    assert jobstore.load_records()[_JOB["id"]]["status"] == "applied"


def test_undo_apply_with_two_applications_only_deletes_correct_one(client):
    jobstore.replace_record(dict(_JOB))
    jobstore.replace_record(dict(_JOB_B))
    app_a = client.post("/data/jobs/apply", json={"id": _JOB["id"]}).json()["application_id"]
    app_b = client.post("/data/jobs/apply", json={"id": _JOB_B["id"]}).json()["application_id"]

    res = client.post("/data/jobs/undo-apply", json={"id": _JOB["id"], "application_id": app_a})
    assert res.status_code == 200

    apps = {a["id"]: a for a in appstore.load_all()}
    assert app_a not in apps
    assert app_b in apps
    assert jobstore.load_records()[_JOB["id"]]["status"] == "viewed"
    assert jobstore.load_records()[_JOB_B["id"]]["status"] == "applied"


def test_apply_twice_is_rejected(client):
    jobstore.replace_record(dict(_JOB))
    first = client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    assert first.status_code == 200

    second = client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    assert second.status_code == 409

    assert len(appstore.load_all()) == 1
    assert jobstore.load_records()[_JOB["id"]]["status"] == "applied"


def test_apply_after_undo_succeeds(client):
    jobstore.replace_record(dict(_JOB))
    app_id = client.post("/data/jobs/apply", json={"id": _JOB["id"]}).json()["application_id"]
    client.post("/data/jobs/undo-apply", json={"id": _JOB["id"], "application_id": app_id})

    res = client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    assert res.status_code == 200
    assert len(appstore.load_all()) == 1
    assert jobstore.load_records()[_JOB["id"]]["status"] == "applied"


def test_apply_persists_job_id(client):
    jobstore.replace_record(dict(_JOB))
    client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    assert appstore.load_all()[0]["job_id"] == _JOB["id"]


def test_undo_apply_cross_job_same_company_and_title_is_rejected(client):
    """The reproduction that broke the company/role heuristic: two DISTINCT
    postings sharing both company AND title (same role advertised through two
    ATS boards). A job_id-based check must still tell them apart — matching on
    company/role alone cannot."""
    jobstore.replace_record(dict(_JOB_TWIN_1))
    jobstore.replace_record(dict(_JOB_TWIN_2))
    app_1 = client.post("/data/jobs/apply", json={"id": _JOB_TWIN_1["id"]}).json()["application_id"]
    app_2 = client.post("/data/jobs/apply", json={"id": _JOB_TWIN_2["id"]}).json()["application_id"]

    # Undo job 1's apply using job 2's application id.
    res = client.post(
        "/data/jobs/undo-apply", json={"id": _JOB_TWIN_1["id"], "application_id": app_2}
    )
    assert res.status_code == 409

    apps = {a["id"]: a for a in appstore.load_all()}
    assert app_1 in apps
    assert app_2 in apps  # neither application was deleted
    assert jobstore.load_records()[_JOB_TWIN_1["id"]]["status"] == "applied"
    assert jobstore.load_records()[_JOB_TWIN_2["id"]]["status"] == "applied"


def test_undo_apply_cross_job_same_company_and_title_correct_pair_succeeds(client):
    jobstore.replace_record(dict(_JOB_TWIN_1))
    jobstore.replace_record(dict(_JOB_TWIN_2))
    app_1 = client.post("/data/jobs/apply", json={"id": _JOB_TWIN_1["id"]}).json()["application_id"]
    app_2 = client.post("/data/jobs/apply", json={"id": _JOB_TWIN_2["id"]}).json()["application_id"]

    res = client.post(
        "/data/jobs/undo-apply", json={"id": _JOB_TWIN_1["id"], "application_id": app_1}
    )
    assert res.status_code == 200

    apps = {a["id"]: a for a in appstore.load_all()}
    assert app_1 not in apps
    assert app_2 in apps  # the other job's application is untouched
    assert jobstore.load_records()[_JOB_TWIN_1["id"]]["status"] == "viewed"
    assert jobstore.load_records()[_JOB_TWIN_2["id"]]["status"] == "applied"


def test_undo_apply_legacy_row_without_job_id_is_rejected(client):
    """An application created before job_id existed (job_id IS NULL) must NOT
    fall back to the company/role heuristic — that would reopen the same
    hole. It is rejected outright and nothing changes."""
    jobstore.replace_record(dict(_JOB))
    legacy = appstore.add_application(_JOB["company"], _JOB["title"], status="applied")
    assert legacy["job_id"] is None

    res = client.post(
        "/data/jobs/undo-apply", json={"id": _JOB["id"], "application_id": legacy["id"]}
    )
    assert res.status_code == 409

    apps = {a["id"]: a for a in appstore.load_all()}
    assert legacy["id"] in apps  # not deleted
    assert jobstore.load_records()[_JOB["id"]]["status"] == "new"  # unchanged
