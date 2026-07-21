"""Extract plain text from an uploaded experience document.

Supports the formats a resume / project write-up actually arrives in: PDF, DOCX,
and plain text / Markdown. The web upload endpoint (added in the Next.js UI
phase) and the CLI both call `extract_text`. Kept dependency-light: pypdf for
PDF, python-docx for DOCX, stdlib for text.
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED = (".pdf", ".docx", ".txt", ".md", ".markdown")


def _from_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n\n".join(pages).strip()


def _from_docx(path: Path) -> str:
    import docx  # python-docx

    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs).strip()


def extract_text(path: str | Path) -> str:
    """Return the plain-text contents of a resume/project file.

    Raises FileNotFoundError if the path is missing and ValueError for an
    unsupported extension.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {p}")

    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _from_pdf(p)
    if suffix == ".docx":
        return _from_docx(p)
    if suffix in (".txt", ".md", ".markdown"):
        return p.read_text(encoding="utf-8", errors="replace").strip()

    raise ValueError(
        f"unsupported file type '{suffix}' (supported: {', '.join(SUPPORTED)})"
    )
