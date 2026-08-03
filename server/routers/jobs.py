"""Job write endpoints — the single writer for `jobs` status changes.

Everything goes through agents.job_scraper.store.set_status, which updates BOTH
the mirrored `status` column and the `data` JSON blob, keeping the Python
pipeline and the Prisma reads in sync.

Apply is deliberately more than a status flip: it records WHICH résumé was used
and pins the exact compiled PDF, because the old one-click version marked a job
"applied" without ever opening the posting or noting the résumé — the tracker
asserted things that had not happened.

Phase B adds the assisted-apply endpoints (`/data/jobs/assisted-apply*` and
`/data/jobs/confirm-submission`). THE ONE RULE for all of Phase B applies to them
too: no code path may ever click a submit button. Nothing here touches a browser
at all — it starts the agent, hands back its report, and reads a verdict from
`agents/job_applier/confirm.py`. In particular there is deliberately **no
endpoint that submits a form**, and the ordering below is what keeps the tracker
honest: an application row is written when the HUMAN says she pressed Submit
(through the existing `/data/jobs/apply`), never when the agent finishes filling.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.application_tracker import store as appstore
from agents.job_applier import session
from agents.job_applier.confirm import stamp_if_confirmed
from agents.job_applier.nodes.load_profile import SUPPORTED_ATS
from agents.job_scraper import store as jobstore
from agents.resume_generator.latex import CompileError
from server import applier_run, resume_pdf

router = APIRouter()


class JobRef(BaseModel):
    id: str = ""


class ApplyBody(BaseModel):
    id: str = ""
    resume_job_id: str | None = None


class ConfirmBody(BaseModel):
    id: str = ""            # the jobs row this application was filed for
    application_id: int = 0


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


# ---------------------------------------------------------------------------
# Assisted apply (Phase B) — the agent fills the form; the human submits it
# ---------------------------------------------------------------------------


@router.post("/data/jobs/assisted-apply")
async def assisted_apply(body: ApplyBody):
    """Start the job-applier agent on this posting. Returns a run id to stream.

    **Nothing is recorded as applied here, and that is the point.** The agent
    fills a form and stops; whether an application exists depends on a human
    pressing Submit afterwards. Writing the tracker row now would recreate
    exactly the bug Phase A's docstring above describes, one step earlier in the
    flow. The row is written by `/data/jobs/apply` when the user says she sent it,
    and `/data/jobs/confirm-submission` is what can then verify it.

    The résumé is resolved to a real file here rather than in the agent:
    `resume_pdf.pdf_path` compiles it if needed and returns the path
    `fill.attach_resume` will hand to the browser. A failure is NOT fatal — the
    run proceeds with no résumé and the handoff says, once, that nothing was
    attached — but it IS reported, as `pdf_error`, for the same reason
    `/data/jobs/apply` reports it: a UI that shows unqualified success while the
    résumé silently went missing is lying.

    `async` is load-bearing: `applier_run.start` needs the running event loop to
    publish node events back to it from the browser's thread. A sync endpoint runs
    on a threadpool worker where there is no running loop to capture.
    """
    jid = body.id.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    rec = _load(jid)
    if rec is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    if rec.get("status") == "applied":
        return JSONResponse({"error": f"Already applied to {jid}."}, status_code=409)

    ats = str(rec.get("ats") or "").strip().lower()
    if ats not in SUPPORTED_ATS:
        # The same refusal `load_profile` makes, made before a browser is opened
        # so the UI can offer the manual path instead of showing a failed run.
        return JSONResponse(
            {
                "error": (
                    f"Assisted apply only knows how to read "
                    f"{', '.join(SUPPORTED_ATS)} forms, and this posting is on "
                    f"“{ats or 'an unknown board'}”. Open it and fill it in "
                    f"yourself."
                ),
                "ats": ats,
            },
            status_code=400,
        )

    resume_job_id = (body.resume_job_id or "").strip() or None
    resume_path, pdf_key, pdf_error = "", None, None
    try:
        pdf_key, path = resume_pdf.pdf_path(resume_job_id)
        resume_path = str(path)
    except (LookupError, CompileError) as exc:
        pdf_error = str(exc)
        print(f"⚠️ Could not prepare résumé PDF for {jid}: {exc}")

    run_id = applier_run.start(jid, resume_path, asyncio.get_running_loop())
    return {
        "ok": True,
        "run_id": run_id,
        "resume_pdf_key": pdf_key,
        # The basename only. The handoff report goes to some length never to show
        # the user an absolute path (decision 8 there); an API response that
        # leaked the home directory would undo it.
        "resume_filename": f"{pdf_key}.pdf" if pdf_key else "",
        "pdf_error": pdf_error,
    }


@router.get("/data/jobs/assisted-apply/report")
def assisted_apply_report(run_id: int = 0):
    """The finished run's handoff report, as structured data.

    Structure rather than the rendered text on purpose: the UI orders and groups
    the items itself (blocking first, filled-and-verified collapsed last), and a
    UI that parsed `render_text()` would break the first time a band heading was
    reworded. 404 means the report is not held — a server restart, or a run that
    has not finished — and the rendered text is still on the run in history.
    """
    payload = applier_run.report_payload(run_id)
    if payload is None:
        return JSONResponse(
            {"error": f"No handoff report held for run {run_id}."}, status_code=404
        )
    return payload


@router.post("/data/jobs/confirm-submission")
def confirm_submission(body: ConfirmBody):
    """Re-read the still-open form and, on a positive match only, verify the row.

    This is the trigger Task 9 deliberately left unwired: the graph ends while the
    human still has to press Submit, so nothing inside a run can ever see a
    confirmation page. A button the user presses when she is done is the honest
    version of that trigger — it is the only moment anything in this system
    actually knows a submission was attempted. Polling the live page would guess
    at that moment, and would keep guessing at it while she was still typing.

    Three outcomes, and none of them is a failure of the application:

      * `checked` false — nothing could be read (no window held, the thread still
        busy, the window closed). A *don't know*, and no database access happens.
      * `checked` true, `confirmed` false — the page was read and does not vouch
        for a submission. Also a don't-know: the row keeps whatever Phase A wrote,
        untouched. `stamp_if_confirmed` performs no write at all on this path.
      * `confirmed` true — `applications.confirmed_at` is stamped, once.

    Nothing here can un-confirm, clear, or delete anything: the only store call
    reachable from this endpoint is `mark_confirmed`, which is guarded to a single
    write-once UPDATE.

    Ownership is verified BEFORE anything is read, with the same exact-`job_id`
    check `undo_apply` uses and for the same reason: `company`/`role` cannot tell
    two open reqs apart, and stamping the wrong row would mark an application
    verified that nothing has looked at.
    """
    jid = body.id.strip()
    app_id = int(body.application_id or 0)
    if not jid or not app_id:
        return JSONResponse(
            {"error": "id and application_id are required."}, status_code=400
        )
    app = next((a for a in appstore.load_all() if a["id"] == app_id), None)
    if app is None:
        return JSONResponse(
            {"error": f"No application with id {app_id}."}, status_code=404
        )
    if app.get("job_id") != jid:
        return JSONResponse(
            {"error": f"Application {app_id} does not belong to job {jid}."},
            status_code=409,
        )

    read = session.read(jid)
    if not read.ok:
        return {"ok": True, "checked": False, "confirmed": False, "reason": read.error}
    result = stamp_if_confirmed(app_id, read.html, read.url)
    return {
        "ok": True,
        "checked": True,
        "confirmed": result.confirmed,
        "reason": result.reason,
        "ats": result.ats,
        "marker": result.marker,
    }


@router.post("/data/jobs/assisted-apply/close")
def close_assisted_window(body: JobRef):
    """Close the browser window the agent left open (all of them when id is "").

    Exposed because the run deliberately does not close it: the user is the one
    who knows when she is finished with the form, and a window nothing can close
    from the UI is a Chromium process she has to find herself. Idempotent — a
    window already gone reports `closed: 0`.
    """
    closed = session.close(body.id.strip() or None)
    return {"ok": True, "closed": int(closed)}
