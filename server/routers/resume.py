"""Experience-pool file upload for the resume generator.

The Next.js pool manager can add pasted text on its own (Prisma), but PDF/DOCX
need the Python parser (pypdf / python-docx). This endpoint takes an uploaded
file, extracts its text with the same `parse_upload` used by the CLI, and stores
it in the experience pool — one source of truth for parsing across CLI and web.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.resume_generator import parse_upload
from agents.resume_generator import store as resume_store
from agents.resume_generator.latex import CompileError, compile_tex
from server.markdown import render_markdown

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


@router.post("/experience/parse")
async def parse_experience(file: UploadFile = File(...)):
    """Extract text from an uploaded file WITHOUT storing it — used to seed the
    master résumé editor from an existing PDF/DOCX/TXT/MD."""
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in parse_upload.SUPPORTED:
        return JSONResponse(
            {"error": f"unsupported file type '{suffix}' (supported: {', '.join(parse_upload.SUPPORTED)})"},
            status_code=400,
        )
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            text = parse_upload.extract_text(tmp.name)
        except (ValueError, OSError) as exc:
            return JSONResponse({"error": f"could not parse file: {exc}"}, status_code=400)
    if not text.strip():
        return JSONResponse({"error": "no text could be extracted from that file"}, status_code=400)
    return {"text": text}


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
    latex: str | None = None
    status: str | None = None


@router.patch("/data/resumes")
def edit_resume(body: ResumeEdit):
    job_id = body.jobId.strip()
    if not job_id:
        return JSONResponse({"error": "jobId is required."}, status_code=400)
    if body.markdown is None and body.latex is None and body.status is None:
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
        latex=body.latex,  # None keeps the existing tailored .tex
        keywords=existing["keywords"],
        status=body.status.strip() if body.status is not None else existing["status"],
    )
    return {"resume": resume}


@router.get("/data/resumes/{job_id}/versions")
def resume_versions(job_id: str):
    """Past snapshots for a resume (newest first)."""
    return {"versions": resume_store.list_resume_versions(job_id)}


class RenderBody(BaseModel):
    markdown: str = ""


@router.post("/data/render")
def render(body: RenderBody):
    """Markdown → HTML, reusing the same renderer as agent output. Used by the
    résumé tab for previews and the print-to-PDF view (no client md dependency)."""
    return {"html": render_markdown(body.markdown)}


class TexBody(BaseModel):
    tex: str = ""
    filename: str = "resume.pdf"


@router.post("/data/resume/pdf")
def compile_pdf(body: TexBody):
    """Compile a full LaTeX document to a real PDF (Tectonic). Returns the PDF
    bytes on success; on a LaTeX error returns 422 with the engine log so the
    UI can fall back to handing the user the raw .tex."""
    try:
        pdf = compile_tex(body.tex)
    except CompileError as exc:
        return JSONResponse({"error": str(exc), "log": exc.log}, status_code=422)
    name = (body.filename or "resume.pdf").strip() or "resume.pdf"
    if not name.endswith(".pdf"):
        name += ".pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --------------------------------------------------------------------------
# Master resume — the single canonical résumé tailored drafts start from
# --------------------------------------------------------------------------
class MasterEdit(BaseModel):
    markdown: str | None = None
    latex: str | None = None
    keywords: list[str] | None = None


@router.get("/data/resume/master")
def get_master():
    return {"master": resume_store.get_master_resume()}


@router.put("/data/resume/master")
def put_master(body: MasterEdit):
    if body.markdown is None and body.latex is None and body.keywords is None:
        return JSONResponse({"error": "Nothing to update."}, status_code=400)
    master = resume_store.upsert_master_resume(
        body.markdown, latex=body.latex, keywords=body.keywords
    )
    return {"master": master}
