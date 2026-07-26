"""Applicant-profile read/write — the single writer for `applicant_profile`.

Typed fields (name, email, phone, links, school, work authorization) feed Phase
B's form autofill deterministically, and `summary` is the candidate description
that drives job fit scoring.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import profile_store

router = APIRouter()


class ProfileEdit(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    school: str | None = None
    degree: str | None = None
    grad_date: str | None = None
    us_work_auth: str | None = None
    ca_work_auth: str | None = None
    needs_sponsorship: bool | None = None
    summary: str | None = None


@router.get("/data/profile")
def get_profile():
    return {"profile": profile_store.get_profile()}


@router.put("/data/profile")
def put_profile(body: ProfileEdit):
    supplied = {k: v for k, v in body.model_dump().items() if v is not None}
    if not supplied:
        return JSONResponse({"error": "Nothing to update."}, status_code=400)
    try:
        profile = profile_store.upsert_profile(**supplied)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"profile": profile}
