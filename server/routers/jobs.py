"""Job write endpoints — the single writer for `jobs` status changes.

apply/dismiss go through agents.job_scraper.store.set_status, which updates BOTH
the mirrored `status` column and the `data` JSON blob — so this replaces the
Next-side dual-writer (jobs-server.ts) with no drift.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.application_tracker import store as appstore
from agents.job_scraper import store as jobstore

router = APIRouter()


class JobRef(BaseModel):
    id: str = ""


@router.post("/jobs/apply")
def apply_to_job(body: JobRef):
    jid = body.id.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    rec = jobstore.load_records().get(jid)
    if rec is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    # Hand the role to the tracker, then mark the posting applied.
    appstore.add_application(rec.get("company", ""), rec.get("title", ""), url=rec.get("url", ""), status="applied")
    jobstore.set_status(jid, "applied")
    return {"ok": True}


@router.post("/jobs/dismiss")
def dismiss_job(body: JobRef):
    jid = body.id.strip()
    if not jid:
        return JSONResponse({"error": "Missing id."}, status_code=400)
    if jobstore.set_status(jid, "dismissed") is None:
        return JSONResponse({"error": f"No job with id {jid}."}, status_code=404)
    return {"ok": True}
