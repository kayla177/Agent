"""Application-tracker CRUD routes (GET page + POST add/status/delete).

Self-contained so it adds the feature with minimal edits to existing web files.
Reads/writes the SAME JSON store the application_tracker agent reads, so logging
here is immediately reflected in the agent's next run.
"""

from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agents.application_tracker import store
from web.templating import templates

router = APIRouter()


@router.get("/applications", response_class=HTMLResponse)
def applications_page(request: Request, saved: int = 0, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "applications.html",
        {
            "apps": store.load_all(),
            "statuses": store.STATUSES,
            "saved": bool(saved),
            "error": error,
        },
    )


def _redirect(error: str | None = None) -> RedirectResponse:
    if error:
        return RedirectResponse(
            url=f"/applications?error={urllib.parse.quote(error)}", status_code=303
        )
    return RedirectResponse(url="/applications?saved=1", status_code=303)


@router.post("/applications/add")
async def add_application(request: Request):
    form = await request.form()

    def g(key: str) -> str:
        return str(form.get(key, "")).strip()

    if not g("company") or not g("role"):
        return _redirect("Company and role are required.")
    try:
        store.add_application(
            company=g("company"),
            role=g("role"),
            url=g("url"),
            notes=g("notes"),
            status=g("status") or "applied",
        )
    except ValueError as exc:
        return _redirect(str(exc))
    return _redirect()


@router.post("/applications/{app_id}/status")
async def set_status(app_id: int, request: Request):
    form = await request.form()
    status = str(form.get("status", "")).strip()
    try:
        updated = store.update_status(app_id, status)
    except ValueError as exc:
        return _redirect(str(exc))
    if updated is None:
        return _redirect(f"No application with id {app_id}.")
    return _redirect()


@router.post("/applications/{app_id}/delete")
async def delete_application(app_id: int):
    store.delete_application(app_id)
    return _redirect()
