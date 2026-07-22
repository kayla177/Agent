"""Experience-pool file upload for the resume generator.

The Next.js pool manager can add pasted text on its own (Prisma), but PDF/DOCX
need the Python parser (pypdf / python-docx). This endpoint takes an uploaded
file, extracts its text with the same `parse_upload` used by the CLI, and stores
it in the experience pool — one source of truth for parsing across CLI and web.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.resume_generator import parse_upload
from agents.resume_generator import store as resume_store

router = APIRouter()


@router.post("/experience/upload")
async def upload_experience(file: UploadFile = File(...), kind: str = Form("resume")):
    if kind not in resume_store.KINDS:
        return JSONResponse({"error": f"kind must be one of {resume_store.KINDS}"}, status_code=400)

    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in parse_upload.SUPPORTED:
        return JSONResponse(
            {"error": f"unsupported file type '{suffix}' (supported: {', '.join(parse_upload.SUPPORTED)})"},
            status_code=400,
        )

    data = await file.read()
    # parse_upload dispatches on the file suffix, so preserve it on the temp file.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            text = parse_upload.extract_text(tmp.name)
        except (ValueError, OSError) as exc:
            return JSONResponse({"error": f"could not parse file: {exc}"}, status_code=400)

    if not text.strip():
        return JSONResponse({"error": "no text could be extracted from that file"}, status_code=400)

    doc_id = resume_store.add_experience_doc(filename, text, kind=kind)
    return JSONResponse({"id": doc_id, "chars": len(text)}, status_code=201)


# --------------------------------------------------------------------------
# Experience pool — text add / delete (single writer for experience_docs)
# --------------------------------------------------------------------------
class NewDoc(BaseModel):
    filename: str = ""
    kind: str = "resume"
    text: str = ""


@router.post("/data/resume/docs")
def add_doc(body: NewDoc):
    """Add one experience doc from pasted text (binary files use /experience/upload)."""
    filename = body.filename.strip() or "pasted.md"
    kind = body.kind.strip()
    if not body.text.strip():
        return JSONResponse({"error": "Text is empty."}, status_code=400)
    if kind not in resume_store.KINDS:
        return JSONResponse({"error": "kind must be resume or project."}, status_code=400)
    doc_id = resume_store.add_experience_doc(filename, body.text, kind=kind)
    return JSONResponse({"id": doc_id}, status_code=201)


@router.delete("/data/resume/docs/{doc_id}")
def delete_doc(doc_id: int):
    if not resume_store.delete_experience_doc(doc_id):
        return JSONResponse({"error": f"No experience doc with id {doc_id}."}, status_code=404)
    return {"ok": True}


# --------------------------------------------------------------------------
# Generated resumes — edit markdown / flip draft<->final (single writer)
# --------------------------------------------------------------------------
class ResumeEdit(BaseModel):
    jobId: str = ""
    markdown: str | None = None
    status: str | None = None


@router.patch("/data/resumes")
def edit_resume(body: ResumeEdit):
    job_id = body.jobId.strip()
    if not job_id:
        return JSONResponse({"error": "jobId is required."}, status_code=400)
    if body.markdown is None and body.status is None:
        return JSONResponse({"error": "Nothing to update."}, status_code=400)
    if body.status is not None and body.status.strip() not in resume_store.STATUSES:
        return JSONResponse({"error": "status must be draft or final."}, status_code=400)

    existing = resume_store.get_resume(job_id)
    if existing is None:
        return JSONResponse({"error": f"No resume for job {job_id}."}, status_code=404)

    resume = resume_store.upsert_resume(
        job_id,
        company=existing["company"],
        role=existing["role"],
        markdown=body.markdown if body.markdown is not None else existing["markdown"],
        keywords=existing["keywords"],
        status=body.status.strip() if body.status is not None else existing["status"],
    )
    return {"resume": resume}
