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
    # Assert on the MESSAGE, not just the code: the next check (job_id mismatch)
    # also returns 409, so deleting the whole legacy-NULL branch left this test
    # green while reopening the hole it exists to close.
    assert "predates job linking" in res.json()["error"]

    apps = {a["id"]: a for a in appstore.load_all()}
    assert legacy["id"] in apps  # not deleted
    assert jobstore.load_records()[_JOB["id"]]["status"] == "new"  # unchanged


def test_undo_apply_mismatch_message_is_distinct_from_the_legacy_one(client):
    """Companion to the assertion above: the two 409 branches must be
    distinguishable, otherwise pinning one of them proves nothing."""
    jobstore.replace_record(dict(_JOB))
    jobstore.replace_record(dict(_JOB_B))
    client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    app_b = client.post("/data/jobs/apply", json={"id": _JOB_B["id"]}).json()["application_id"]

    res = client.post("/data/jobs/undo-apply", json={"id": _JOB["id"], "application_id": app_b})
    assert res.status_code == 409
    assert "does not belong to job" in res.json()["error"]
    assert "predates job linking" not in res.json()["error"]


# --- apply: both writes must land, or neither ---------------------------------


def test_apply_rolls_back_the_application_if_the_job_vanishes(client, monkeypatch):
    """`set_status` returns None when the row is gone. Discarding that return
    produced a 200 `{ok:true}` carrying an application_id for an application that
    is orphaned — no `applied` flag on the job, so the tracker asserts something
    that did not happen. `undo_apply`'s own docstring promises validate-then-
    mutate; apply is held to the same standard."""
    from server.routers import jobs as jobs_router

    jobstore.replace_record(dict(_JOB))
    monkeypatch.setattr(jobs_router.jobstore, "set_status", lambda *a, **k: None)

    res = client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    assert res.status_code == 404
    assert appstore.load_all() == [], "the tracker row must be rolled back, not orphaned"
    assert jobstore.load_records()[_JOB["id"]]["status"] == "new"


def test_apply_reports_a_pdf_failure_instead_of_claiming_plain_success(client, monkeypatch):
    """A résumé PDF that cannot be produced must be REPORTED. It used to be
    logged to stderr while the response said 200 and the UI showed a green
    "Application logged." banner and opened a tab rendering a 422 JSON error.
    Recording the application anyway is correct and deliberate."""
    from agents.resume_generator.latex import CompileError
    from server.routers import jobs as jobs_router

    jobstore.replace_record(dict(_JOB))

    def boom(_job_id):
        raise CompileError("tectonic exploded", log="! Undefined control sequence.")

    monkeypatch.setattr(jobs_router.resume_pdf, "ensure_pdf", boom)

    res = client.post("/data/jobs/apply", json={"id": _JOB["id"]})
    assert res.status_code == 200
    body = res.json()
    assert body["resume_pdf_key"] is None
    assert "tectonic exploded" in body["pdf_error"]
    # Still recorded — that behavior is correct.
    assert len(appstore.load_all()) == 1
    assert jobstore.load_records()[_JOB["id"]]["status"] == "applied"


def test_apply_reports_no_pdf_error_on_the_happy_path(client, monkeypatch):
    from server.routers import jobs as jobs_router

    jobstore.replace_record(dict(_JOB))
    monkeypatch.setattr(jobs_router.resume_pdf, "ensure_pdf", lambda j: ("k__1", b"%PDF-1.4"))

    body = client.post("/data/jobs/apply", json={"id": _JOB["id"]}).json()
    assert body["pdf_error"] is None
    assert body["resume_pdf_key"] == "k__1"
    assert appstore.load_all()[0]["resume_pdf_key"] == "k__1"


# --- dismiss ------------------------------------------------------------------


def test_dismiss_sets_the_status_in_column_and_blob(client):
    jobstore.replace_record(dict(_JOB))
    assert client.post("/data/jobs/dismiss", json={"id": _JOB["id"]}).status_code == 200
    assert jobstore.load_records()[_JOB["id"]]["status"] == "dismissed"
    with jobstore.store_db.connect() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (_JOB["id"],)).fetchone()
    assert row["status"] == "dismissed"


def test_dismiss_unknown_job_404(client):
    res = client.post("/data/jobs/dismiss", json={"id": "nope"})
    assert res.status_code == 404
    assert "nope" in res.json()["error"]


def test_dismiss_missing_id_400(client):
    assert client.post("/data/jobs/dismiss", json={"id": "   "}).status_code == 400
    assert client.post("/data/jobs/dismiss", json={}).status_code == 400


# --- resume-pdf ---------------------------------------------------------------


def test_resume_pdf_404_when_no_resume_exists(client):
    """No master résumé set at all -> LookupError -> 404, not a 500."""
    res = client.get("/data/jobs/resume-pdf")
    assert res.status_code == 404
    assert "error" in res.json()


def test_resume_pdf_404_for_an_unknown_job(client):
    res = client.get("/data/jobs/resume-pdf", params={"job_id": "Nope:greenhouse:1"})
    assert res.status_code == 404
    assert "Nope:greenhouse:1" in res.json()["error"]


def test_resume_pdf_422_on_a_compile_failure_includes_the_log(client, monkeypatch):
    """A LaTeX failure is a 422 carrying the engine log, so the UI can offer the
    raw .tex rather than showing an opaque error."""
    from agents.resume_generator import store as rstore
    from agents.resume_generator.latex import CompileError
    from server import resume_pdf as pdf_mod

    rstore.upsert_master_resume(latex="\\documentclass{article}\\begin{document}x\\end{document}")

    def boom(_tex, **_kw):
        raise CompileError("latex failed", log="! Undefined control sequence.")

    monkeypatch.setattr(pdf_mod, "compile_tex", boom)

    res = client.get("/data/jobs/resume-pdf")
    assert res.status_code == 422
    body = res.json()
    assert "latex failed" in body["error"]
    assert "Undefined control sequence" in body["log"]


def test_resume_pdf_serves_the_pdf_with_a_filename(client, monkeypatch, tmp_path):
    from agents.resume_generator import store as rstore
    from server import resume_pdf as pdf_mod

    rstore.upsert_master_resume(latex="\\documentclass{article}\\begin{document}x\\end{document}")
    # Never write into the real data/resumes/ from a test.
    monkeypatch.setattr(pdf_mod, "PDF_DIR", tmp_path / "resumes")
    monkeypatch.setattr(pdf_mod, "compile_tex", lambda _tex, **_kw: b"%PDF-1.4 fake")

    res = client.get("/data/jobs/resume-pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"].startswith('attachment; filename="master__')
