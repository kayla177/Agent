"""Application-tracker write endpoints — the single writer for `applications`.

Thin wrappers over agents.application_tracker.store so the backend owns all DB
writes; Next.js reads via Prisma and mutates through these (proxied to :8001).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.application_tracker import store as appstore

router = APIRouter()

_STATUSES = ", ".join(appstore.STATUSES)


def _out(app: dict) -> dict:
    # Match the Prisma read shape (auto_detected as 0/1, not bool).
    return {**app, "auto_detected": int(app.get("auto_detected", 0))}


class NewApplication(BaseModel):
    company: str = ""
    role: str = ""
    url: str = ""
    notes: str = ""
    status: str = "applied"


@router.post("/applications")
def create_application(body: NewApplication):
    company, role = body.company.strip(), body.role.strip()
    if not company or not role:
        return JSONResponse({"error": "Company and role are required."}, status_code=400)
    status = (body.status or "applied").strip() or "applied"
    if status not in appstore.STATUSES:
        return JSONResponse({"error": f"status must be one of {_STATUSES}"}, status_code=400)
    app = appstore.add_application(company, role, url=body.url.strip(), notes=body.notes.strip(), status=status)
    return JSONResponse({"application": _out(app)}, status_code=201)


class StatusUpdate(BaseModel):
    status: str = ""
    auto_detected: bool = False


@router.patch("/applications/{app_id}/status")
def update_application_status(app_id: int, body: StatusUpdate):
    status = body.status.strip()
    if status not in appstore.STATUSES:
        return JSONResponse({"error": f"status must be one of {_STATUSES}"}, status_code=400)
    app = appstore.update_status(app_id, status, auto_detected=body.auto_detected)
    if app is None:
        return JSONResponse({"error": f"No application with id {app_id}."}, status_code=404)
    return {"application": _out(app)}


@router.delete("/applications/{app_id}")
def delete_application(app_id: int):
    if not appstore.delete_application(app_id):
        return JSONResponse({"error": f"No application with id {app_id}."}, status_code=404)
    return {"ok": True}
