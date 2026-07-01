"""Résumé-tailor routes: paste a job description, get a tailored résumé + cover
letter, optionally save a draft and log the application to the tracker.

The tailor handler is a sync def so FastAPI runs it in a threadpool — the blocking
graph.invoke() (two LLM calls) won't stall the event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from agents.application_tracker import store as tracker_store
from agents.resume_tailor.graph import build_resume_tailor_graph
from agents.resume_tailor.store import (
    base_resume_status,
    list_drafts,
    save_draft,
    smart_role,
)
from web.templating import templates

router = APIRouter()


def _page_context(request: Request, **extra):
    ctx = {
        "base": base_resume_status(),
        "drafts": list_drafts(),
        "result": None,
        "error": None,
        "form": {"company": "", "role": "", "job_description": ""},
        "saved_path": None,
        "mode": smart_role(),  # "local" or "smart" — shown as a badge
    }
    ctx.update(extra)
    return templates.TemplateResponse(request, "resume.html", ctx)


@router.get("/resume", response_class=HTMLResponse)
def resume_page(request: Request):
    return _page_context(request)


@router.post("/resume/tailor", response_class=HTMLResponse)
def tailor(
    request: Request,
    company: str = Form(""),
    role: str = Form(""),
    job_description: str = Form(""),
    log: str = Form(""),
):
    form = {"company": company, "role": role, "job_description": job_description}

    if not job_description.strip():
        return _page_context(request, form=form, error="Paste a job description first.")

    graph = build_resume_tailor_graph(send=False)
    out = graph.invoke(
        {"job_description": job_description, "company": company, "role": role}
    )

    if out.get("error"):
        return _page_context(request, form=form, error=out["error"])

    message = out.get("message", "")
    saved = save_draft(company or "company", role or "role", message)

    # Optionally log the application to the tracker.
    if log:
        try:
            tracker_store.add_application(
                company=company or "(unknown)", role=role or "(unknown)", status="applied"
            )
        except Exception:
            pass

    return _page_context(
        request,
        form=form,
        result=message,
        saved_path=saved.name,
    )
