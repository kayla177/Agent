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
    is still recorded, just without a pinned PDF key.
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
    try:
        # resume_job_id=None selects the master résumé.
        pdf_key, _ = resume_pdf.ensure_pdf(resume_job_id)
    except (LookupError, CompileError) as exc:
        # Not fatal: still log the application, just without a pinned PDF.
        print(f"⚠️ Could not prepare résumé PDF for {jid}: {exc}")

    app = appstore.add_application(
        rec.get("company", ""), rec.get("title", ""),
        url=rec.get("url", ""), status="applied",
        resume_job_id=resume_job_id, resume_pdf_key=pdf_key,
    )
    jobstore.set_status(jid, "applied")
    return {"ok": True, "application_id": int(app["id"]), "resume_pdf_key": pdf_key}


@router.post("/data/jobs/undo-apply")
def undo_apply(body: UndoBody):
    """Reverse an apply: delete the tracker row and un-apply the job.

    The job goes back to `viewed`, not `new`. Reaching the apply modal at all
    means the user opened and read the posting, so `new` would wrongly present
    it as unread in a 130-row list — and `viewed` is exactly the state the row
    was in immediately before Apply was clicked.

    There is no foreign key from `applications` back to `jobs.id`, so before
    mutating anything this verifies the application actually belongs to this
    job by matching the fields `apply` itself wrote: `company` and `role` vs.
    the job's `company` and `title`. Validate-then-mutate, strictly in that
    order — never delete or flip status on an unverified pair.
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
    if app.get("company") != rec.get("company") or app.get("role") != rec.get("title"):
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
