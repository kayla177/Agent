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
