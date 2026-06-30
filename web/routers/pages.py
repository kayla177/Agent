"""Page routes: dashboard, run detail, history, settings (full HTML pages)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from agents.registry import get_spec, list_specs
from web import db, prefs
from web.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"specs": list_specs(), "recent": db.list_runs(limit=10)},
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int):
    run = db.get_run(run_id)
    if not run:
        return templates.TemplateResponse(
            request, "not_found.html", {"what": f"run {run_id}"}, status_code=404
        )
    try:
        spec = get_spec(run["agent_key"])
    except KeyError:
        spec = None
    # Collapse the event log to one line per node (final status), preserving
    # first-seen order — matches how the live JS replaces lines by node.
    nodes: list[dict] = []
    index: dict[str, dict] = {}
    for ev in db.get_node_events(run_id):
        item = index.get(ev["node"])
        if item is None:
            item = {"node": ev["node"], "status": ev["status"]}
            index[ev["node"]] = item
            nodes.append(item)
        else:
            item["status"] = ev["status"]
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": run,
            "spec": spec,
            "nodes": nodes,
            "live": run["status"] == "running",
        },
    )


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, agent: str | None = None):
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "specs": list_specs(),
            "selected": agent,
            "runs": db.list_runs(agent_key=agent, limit=100),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "prefs": prefs.current(),
            "secrets": prefs.secret_status(),
            "saved": bool(saved),
            "error": error,
        },
    )
