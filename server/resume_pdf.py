"""Content-versioned résumé PDF cache.

Compiling with Tectonic takes ~2.0s (measured 2026-07-25), so this exists for
PROVENANCE rather than speed. The key embeds the résumé's `updated_at`, so
editing a résumé writes a NEW file and leaves the old one intact; an application
row pins the key it used (`applications.resume_pdf_key`), which keeps "what
exactly did this company receive?" answerable forever.

Files land in data/resumes/ (gitignored). Nothing evicts them — a few dozen KB
each is a price worth paying for an auditable record.

Writes are atomic (temp file in the same directory + ``os.replace``) and every
cache hit is checked for the ``%PDF`` magic before being served, because a
content-versioned key means a half-written file would otherwise be handed out as
a valid PDF forever, with no way for the cache to self-heal.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

import config
from agents.resume_generator.latex import compile_tex
from agents.resume_generator.store import get_master_resume, get_resume

PDF_DIR = config.PROJECT_ROOT / "data" / "resumes"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def cache_key(job_id: str | None, updated_at: str) -> str:
    """Filesystem-safe, content-versioned, COLLISION-FREE filename stem.

    `job_id` contains ':' (e.g. 'Databricks:greenhouse:7586263002') and can
    contain '/', so both are replaced with '_' for readability — but that
    substitution alone is lossy: 'a:b', 'a/b', and 'a_b' would all sanitize to
    the same 'a_b'. Two DIFFERENT jobs colliding on one cache file would mean
    ensure_pdf silently hands back another company's PDF on a "hit" — exactly
    the provenance guarantee this module exists to provide. So the sanitized
    stem is kept only for human-scannability; an 8-hex-char sha256 digest of
    the RAW (unsanitized) job_id is what actually guarantees distinct job ids
    never share a file. Do not drop the digest to "simplify" this.

    The master résumé has no job_id to collide on, so its key stays a plain
    `master__<version>` with no digest.
    """
    version = _UNSAFE.sub("_", (updated_at or "0"))
    if not job_id:
        return f"master__{version}"
    stem = _UNSAFE.sub("_", job_id)
    digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}__{version}"


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
        cached = path.read_bytes()
        # A cache hit is only a hit if the bytes are actually a PDF. The key is
        # content-versioned, so a file truncated by a crash or a full disk would
        # otherwise be served as `200 application/pdf` forever and get pinned
        # into applications.resume_pdf_key — the cache could never self-heal.
        # Treat a corrupt file as a miss and recompile over it.
        if cached.startswith(b"%PDF"):
            return key, cached
        print(f"⚠️ cached PDF {path.name} is not a PDF ({len(cached)} bytes) — recompiling")

    pdf = compile_tex(tex)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a reader never observes a partial file: the temp file
    # lives in PDF_DIR (same filesystem), which is what makes os.replace atomic.
    fd, tmp = tempfile.mkstemp(dir=PDF_DIR, prefix=".tmp-", suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(pdf)
        os.replace(tmp, path)
    except BaseException:
        # Never leave a stray temp file behind on a failed write.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return key, pdf


def pdf_path(job_id: str | None) -> tuple[str, Path]:
    """`(cache_key, absolute path)` of a résumé PDF on disk, compiling on a miss.

    This exists because the job-applier agent takes a `resume_path`, not bytes:
    `fill.attach_resume` hands the path to Playwright's `set_input_files`, and
    without one the handoff says (correctly, and uselessly) that no résumé was
    attached. `ensure_pdf` already writes `PDF_DIR / f"{key}.pdf"` atomically, so
    this is a two-line accessor rather than a second cache.

    **The path is derived from the key `ensure_pdf` RETURNS — never recomputed by
    calling `cache_key()` again.** That is the whole point of the function. The
    two would disagree on a real, already-shipped path: a tailored résumé with no
    LaTeX of its own falls back to the master's source, and `ensure_pdf`
    deliberately returns the MASTER's key for it (see `key_job`). Recomputing
    `cache_key(job_id, ...)` here would name a file that does not exist, and the
    agent would be handed a path to nothing.

    Raises exactly what `ensure_pdf` raises — `LookupError` when there is no
    résumé to compile, `CompileError` when LaTeX fails — so callers keep the one
    error contract. Neither is fatal to an application: the caller may start the
    run with no résumé path at all and say so.
    """
    key, _ = ensure_pdf(job_id)
    return key, PDF_DIR / f"{key}.pdf"
