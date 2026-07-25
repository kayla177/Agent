"""Latexify node — tailor the master .tex résumé to the target job.

The user keeps a real LaTeX résumé (their template) as the master. Here we ask
the model to produce a job-tailored copy that keeps the document's structure and
macros byte-for-byte and only rewords/reorders the bullet CONTENT to emphasize
job-relevant experience — same truthfulness rules as the Markdown draft.

Small local models can emit broken LaTeX, so this is defensive:
  - no master template  -> skip (the Markdown draft is still saved),
  - model output doesn't compile (or isn't a full document) -> fall back to the
    master .tex verbatim, so the résumé still exports a correct-format PDF.
Either way it never sets `error`; the Markdown draft remains the primary output.
"""

from __future__ import annotations

from agents.resume_generator.latex import CompileError, compile_tex
from agents.resume_generator.state import ResumeState
from shell.model_router import llm

_MAX_TOKENS = 4096

_SYSTEM = (
    "You tailor a candidate's REAL LaTeX résumé to a specific job. You are given "
    "a complete, working LaTeX document and must return a complete, working "
    "LaTeX document.\n\n"
    "ABSOLUTE RULES:\n"
    "- Output ONLY the LaTeX source — start with \\documentclass and end with "
    "\\end{document}. No markdown fences, no commentary.\n"
    "- Keep the preamble, custom macros (\\resumeItem, \\resumeSubheading, etc.), "
    "and overall structure EXACTLY as given. Do not add or remove packages.\n"
    "- Change only the wording inside bullet/item bodies, and you MAY reorder "
    "bullets and entries to surface the most job-relevant experience first.\n"
    "- Use ONLY facts already in the résumé. Never invent employers, titles, "
    "dates, schools, degrees, metrics, or technologies. Mirror the posting's "
    "wording only where it truthfully describes real experience.\n"
    "- Do not add new sections or a 'keyword gaps' block; this is the polished "
    "document, not a worksheet."
)


def _prompt(job: dict, keywords: list[str], master_latex: str, research: str) -> str:
    kw = ", ".join(keywords) if keywords else "(none extracted)"
    lines = [
        f"TARGET ROLE: {job.get('title', '?')} @ {job.get('company', '?')}",
        f"TARGET ATS KEYWORDS (weave in only where truthful): {kw}",
    ]
    if research:
        lines += ["", "POSTING CONTEXT (tone/priorities):", research[:1200]]
    lines += [
        "",
        "MASTER RÉSUMÉ (LaTeX to tailor — preserve structure, reword content):",
        master_latex,
        "",
        "Return the tailored LaTeX document now.",
    ]
    return "\n".join(lines)


def _clean(out: str) -> str:
    """Strip stray markdown fences a model might wrap around the source."""
    out = (out or "").strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[-1] if "\n" in out else out
        if out.endswith("```"):
            out = out[: out.rfind("```")]
    return out.strip()


def latexify_node(state: ResumeState) -> ResumeState:
    if state.get("error"):
        return {}

    master_latex = state.get("master_latex", "")
    if not master_latex:
        return {}  # no template to tailor; Markdown draft stands alone

    job = state.get("job") or {}
    warnings = list(state.get("warnings", []))

    try:
        raw = llm(
            "local",
            _prompt(job, state.get("keywords", []), master_latex, state.get("company_research", "")),
            system=_SYSTEM,
            temperature=0.2,
            max_tokens=_MAX_TOKENS,
        )
        tailored = _clean(raw)
        if not tailored.startswith("\\documentclass") or "\\end{document}" not in tailored:
            raise CompileError("model did not return a full LaTeX document")
        compile_tex(tailored)  # verify it actually builds
        return {"latex": tailored}
    except (CompileError, Exception) as exc:  # noqa: BLE001 — any failure -> safe fallback
        warnings.append(
            f"Couldn't tailor the LaTeX ({type(exc).__name__}); used your master "
            "résumé format untailored for the PDF."
        )
        return {"latex": master_latex, "warnings": warnings}
