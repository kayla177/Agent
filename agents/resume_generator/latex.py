"""Compile a LaTeX résumé to PDF with Tectonic.

Tectonic is a self-contained LaTeX engine (single binary, downloads packages on
demand and caches them). We shell out to it in a throwaway temp dir so nothing
is persisted: the caller gets PDF bytes back, or a CompileError carrying the TeX
log so the UI can fall back to handing the user the raw .tex (option 3).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import config


class CompileError(RuntimeError):
    """LaTeX failed to compile. `log` holds the (truncated) engine output."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


def compile_tex(tex: str, *, timeout: int = 120) -> bytes:
    """Compile a full LaTeX document to PDF and return the PDF bytes.

    Raises CompileError (with the tail of the engine log) on any failure —
    bad LaTeX, a missing engine, or a timeout — so callers never get a partial
    or stale PDF.
    """
    if not tex.strip():
        raise CompileError("Empty LaTeX source.")

    with tempfile.TemporaryDirectory(prefix="resume-tex-") as tmp:
        d = Path(tmp)
        src = d / "resume.tex"
        src.write_text(tex, encoding="utf-8")
        try:
            proc = subprocess.run(
                [config.TECTONIC_BIN, "--outdir", str(d), "--chatter", "minimal", str(src)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(d),
            )
        except FileNotFoundError as exc:
            raise CompileError(f"Tectonic not found ({config.TECTONIC_BIN}).") from exc
        except subprocess.TimeoutExpired as exc:
            raise CompileError(f"LaTeX compile timed out after {timeout}s.") from exc

        pdf = d / "resume.pdf"
        if proc.returncode != 0 or not pdf.exists():
            log = (proc.stderr or "") + (proc.stdout or "")
            raise CompileError("LaTeX compile failed.", log=_tail(log))
        return pdf.read_bytes()


def _tail(text: str, limit: int = 4000) -> str:
    """Last `limit` chars of the log — the errors are always at the end."""
    text = text.strip()
    return text if len(text) <= limit else "…\n" + text[-limit:]
