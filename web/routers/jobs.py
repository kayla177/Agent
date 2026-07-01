"""Job-scraper web view (GET page + POST apply/dismiss).

Reads the SAME record store the job_scraper agent writes
(``agents/job_scraper/data/jobs.json``), so roles surfaced by a run show up here
immediately. "Mark applied" hands the role to the application_tracker store, so
one click moves a scraped role into the pipeline the tracker agent reports on.

Posting ids can contain ``:`` and ``/`` (Workday), so mutating routes take the
id as a form field rather than a path segment.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agents.application_tracker import store as tracker
from agents.job_scraper import store as jobstore
from web.templating import templates

router = APIRouter()

# Sort options exposed in the UI -> key function over a record dict.
_SORTS = {
    "fit": lambda r: (r.get("fit_score") is not None, r.get("fit_score") or 0),
    "date": lambda r: r.get("posted_at") or "",
}


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    sort: str = "fit",
    status: str = "",
    company: str = "",
    hide_ghost: int = 0,
    saved: int = 0,
    error: str | None = None,
):
    records = list(jobstore.load_records().values())

    if status:
        records = [r for r in records if r.get("status") == status]
    if company:
        records = [r for r in records if r.get("company") == company]
    if hide_ghost:
        records = [r for r in records if not r.get("ghost")]

    keyfn = _SORTS.get(sort, _SORTS["fit"])
    records.sort(key=keyfn, reverse=True)

    companies = sorted({r.get("company", "") for r in jobstore.load_records().values() if r.get("company")})

    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": records,
            "companies": companies,
            "statuses": jobstore.STATUSES,
            "sort": sort,
            "status": status,
            "company": company,
            "hide_ghost": bool(hide_ghost),
            "saved": bool(saved),
            "error": error,
        },
    )


def _redirect(error: str | None = None) -> RedirectResponse:
    if error:
        import urllib.parse

        return RedirectResponse(
            url=f"/jobs?error={urllib.parse.quote(error)}", status_code=303
        )
    return RedirectResponse(url="/jobs?saved=1", status_code=303)


@router.post("/jobs/apply")
async def apply_job(request: Request):
    form = await request.form()
    pid = str(form.get("pid", "")).strip()
    record = jobstore.load_records().get(pid)
    if record is None:
        return _redirect(f"No job with id {pid}.")
    try:
        tracker.add_application(
            company=record.get("company", ""),
            role=record.get("title", ""),
            url=record.get("url", ""),
            status="applied",
        )
        jobstore.set_status(pid, "applied")
    except ValueError as exc:
        return _redirect(str(exc))
    return _redirect()


@router.post("/jobs/dismiss")
async def dismiss_job(request: Request):
    form = await request.form()
    pid = str(form.get("pid", "")).strip()
    if jobstore.set_status(pid, "dismissed") is None:
        return _redirect(f"No job with id {pid}.")
    return _redirect()
