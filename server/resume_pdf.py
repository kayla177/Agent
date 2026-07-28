"""Content-versioned résumé PDF cache.

Compiling with Tectonic takes ~2.0s (measured 2026-07-25), so this exists for
PROVENANCE rather than speed. The key embeds the résumé's `updated_at`, so
editing a résumé writes a NEW file and leaves the old one intact; an application
row pins the key it used (`applications.resume_pdf_key`), which keeps "what
exactly did this company receive?" answerable forever.

Files land in data/resumes/ (gitignored). Nothing evicts them — a few dozen KB
each is a price worth paying for an auditable record.
"""

from __future__ import annotations

import re

import config
from agents.resume_generator.latex import compile_tex
from agents.resume_generator.store import get_master_resume, get_resume

PDF_DIR = config.PROJECT_ROOT / "data" / "resumes"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def cache_key(job_id: str | None, updated_at: str) -> str:
    """Filesystem-safe, content-versioned filename stem.

    `job_id` contains ':' (e.g. 'Databricks:greenhouse:7586263002') and can
    contain '/', so both are collapsed to '_'.
    """
    stem = "master" if not job_id else _UNSAFE.sub("_", job_id)
    version = _UNSAFE.sub("_", (updated_at or "0"))
    return f"{stem}__{version}"


def ensure_pdf(job_id: str | None) -> tuple[str, bytes]:
    """Return (cache_key, pdf_bytes) for a résumé, compiling only on a miss.

    `job_id=None` selects the master résumé. Raises LookupError when the résumé
    does not exist or has no LaTeX, and CompileError when LaTeX fails (the
    caller surfaces the engine log so the UI can offer the raw .tex).
    """
    # `key_job` is what the cache key is built from, and it must describe the
    # CONTENT actually compiled. A tailored résumé that was never latexified
    # falls back to the master's LaTeX (the same fallback the latexify node
    # uses) — and in that case the key must be the MASTER's key, not the job's.
    # Otherwise an application pins e.g. "Snowflake_..." while the bytes sent
    # were the generic master résumé, and the pinned key — whose entire purpose
    # is answering "what exactly did this company receive?" — would lie.
    key_job: str | None = job_id or None

    if job_id:
        rec = get_resume(job_id)
        if rec is None:
            raise LookupError(f"no résumé for job {job_id}")
        tex, updated_at = rec.get("latex", ""), rec.get("updated_at", "")
        if not tex.strip():
            master = get_master_resume()
            tex, updated_at = master.get("latex", ""), master.get("updated_at", "")
            key_job = None  # these bytes ARE the master résumé
    else:
        master = get_master_resume()
        tex, updated_at = master.get("latex", ""), master.get("updated_at", "")

    if not tex.strip():
        raise LookupError("no LaTeX résumé available — set your master résumé first")

    key = cache_key(key_job, updated_at)
    path = PDF_DIR / f"{key}.pdf"
    if path.exists():
        return key, path.read_bytes()

    pdf = compile_tex(tex)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    return key, pdf
