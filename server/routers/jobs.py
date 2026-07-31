"""Job write endpoints — the single writer for `jobs` status changes.

Everything goes through agents.job_scraper.store.set_status, which updates BOTH
the mirrored `status` column and the `data` JSON blob, keeping the Python
pipeline and the Prisma reads in sync.

Apply is deliberately more than a status flip: it records WHICH résumé was used
and pins the exact compiled PDF, because the old one-click version marked a job
"applied" without ever opening the posting or noting the résumé — the tracker
asserted things that had not happened.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore
from agents.resume_generator.latex import CompileError
from server import resume_pdf

router = APIRouter()


class JobRef(BaseModel):
    id: str = ""


class ApplyBody(BaseModel):
    id: str = ""
    resume_job_id: str | None = None


class UndoBody(BaseModel):
    id: str = ""
    application_id: int = 0


class StatusBody(BaseModel):
    id: str = ""
    status: str = ""


def _load(jid: str):
    return jobstore.load_records().get(jid)


@router.post("/data/jobs/apply")
def apply_to_job(body: ApplyBody):
    """Record an application for a scraped job, linking the résumé used.

    The caller (the apply modal) opens the posting itself and offers Undo; this
    endpoint only writes. A PDF-compile failure is NOT fatal — the application
    is still recorded, just without a pinned PDF key — but it IS reported, as
    `pdf_error` on the 200 response. Swallowing it into a stderr log meant the
    UI showed a green "Application logged." banner and then opened a tab
    rendering a 422 JSON error, which is not an honest success.

    Both writes are kept consistent: if the job row vanishes between the
    validation above and the status flip, the application row just created is
    deleted again, so the endpoint never returns 200 over an orphaned
    application with no `applied` flag (`undo_apply` holds itself to the same
    validate-then-mutate standard).
    """
    jid = body.id.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    rec = _load(jid)
    if rec is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    if rec.get("status") == "applied":
        return JSONResponse(
            {"error": f"Already applied to {jid}."}, status_code=409
        )

    resume_job_id = (body.resume_job_id or "").strip() or None
    pdf_key = None
    pdf_error: str | None = None
    try:
        # resume_job_id=None selects the master résumé.
        pdf_key, _ = resume_pdf.ensure_pdf(resume_job_id)
    except (LookupError, CompileError) as exc:
        # Not fatal: still log the application, just without a pinned PDF. The
        # caller is told, so it can skip opening a PDF tab that would only
        # render an error and can say plainly that nothing was attached.
        pdf_error = str(exc)
        print(f"⚠️ Could not prepare résumé PDF for {jid}: {exc}")

    app = appstore.add_application(
        rec.get("company", ""), rec.get("title", ""),
        url=rec.get("url", ""), status="applied",
        resume_job_id=resume_job_id, resume_pdf_key=pdf_key,
        job_id=jid,
    )
    if jobstore.set_status(jid, "applied") is None:
        # The job row disappeared between _load and here. Roll the tracker row
        # back rather than leaving an application for a job that is not marked
        # applied and can no longer be undone through the UI.
        appstore.delete_application(int(app["id"]))
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    return {
        "ok": True,
        "application_id": int(app["id"]),
        "resume_pdf_key": pdf_key,
        "pdf_error": pdf_error,
    }


@router.post("/data/jobs/undo-apply")
def undo_apply(body: UndoBody):
    """Reverse an apply: delete the tracker row and un-apply the job.

    The job goes back to `viewed`, not `new`. Reaching the apply modal at all
    means the user opened and read the posting, so `new` would wrongly present
    it as unread in a 130-row list — and `viewed` is exactly the state the row
    was in immediately before Apply was clicked.

    Ownership is verified via `applications.job_id`, an exact link to the
    `jobs.id` this row was filed for. Matching on `company`/`role` instead
    (an earlier version of this endpoint did) is NOT enough: job ids are
    `f"{company}:{ats}:{native_id}"`, so the same company advertising the
    same title through two different ATS boards — or two open reqs with an
    identical title — produce two distinct `jobs` rows with identical
    `company`/`title`, and a heuristic match cannot tell them apart. A row
    with `job_id IS NULL` predates this link (created before it existed) and
    is deliberately NOT matched by falling back to the heuristic — that would
    just reopen the same hole for exactly the rows most likely to collide.
    Validate-then-mutate, strictly in that order — never delete or flip
    status on an unverified pair.
    """
    jid = body.id.strip()
    if not jid or not body.application_id:
        return JSONResponse({"error": "id and application_id are required."}, status_code=400)
    rec = _load(jid)
    if rec is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)

    app = next((a for a in appstore.load_all() if a["id"] == body.application_id), None)
    if app is None:
        return JSONResponse(
            {"error": f"No application with id {body.application_id}."}, status_code=404
        )
    if app.get("job_id") is None:
        return JSONResponse(
            {"error": f"Application {body.application_id} predates job linking "
                      "and cannot be undone automatically."},
            status_code=409,
        )
    if app.get("job_id") != jid:
        return JSONResponse(
            {"error": f"Application {body.application_id} does not belong to job {jid}."},
            status_code=409,
        )

    if not appstore.delete_application(body.application_id):
        # Deleted out from under us between the lookup and here — do not
        # touch the job's status for a delete that did not happen.
        return JSONResponse(
            {"error": f"No application with id {body.application_id}."}, status_code=404
        )
    jobstore.set_status(jid, "viewed")
    return {"ok": True}


@router.post("/data/jobs/dismiss")
def dismiss_job(body: JobRef):
    jid = body.id.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    if jobstore.set_status(jid, "dismissed") is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    return {"ok": True}


@router.post("/data/jobs/status")
def set_job_status(body: StatusBody):
    """Generic status setter — powers 'viewed' on expand and Restore on a
    dismissed row."""
    jid = body.id.strip()
    status = body.status.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    if status not in jobstore.STATUSES:
        return JSONResponse(
            {"error": f"status must be one of {', '.join(jobstore.STATUSES)}"}, status_code=400
        )
    if jobstore.set_status(jid, status) is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    return {"ok": True}


@router.get("/data/jobs/resume-pdf")
def get_resume_pdf(job_id: str = ""):
    """Compiled PDF for a résumé (master when job_id is omitted), so the apply
    modal can hand the user the exact file to upload."""
    try:
        key, pdf = resume_pdf.ensure_pdf(job_id.strip() or None)
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except CompileError as exc:
        return JSONResponse({"error": str(exc), "log": exc.log}, status_code=422)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{key}.pdf"'},
    )
