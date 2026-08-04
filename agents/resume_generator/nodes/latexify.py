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

from agents.resume_generator.latex import compile_tex
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


_OPEN = "\\documentclass"
_CLOSE = "\\end{document}"


def _clean(out: str) -> str:
    """Reduce a model reply to just the LaTeX source it contains.

    Models wrap source in two ways, and only one of them used to be handled.
    Markdown fences were stripped; **prose was not**, and that is what made LaTeX
    tailoring fail 100% of the time. Measured 2026-08-04 against
    `ollama/llama3.1:8b`, the reply began:

        'Here is the tailored LaTeX document:\\n\\n\\n\\documentclass[letterpaper...'

    Those 39 characters meant the caller's `startswith(_OPEN)` was False, so a
    perfectly valid document was discarded unread — it compiled to a 33,575-byte
    PDF once the prefix was sliced off.

    So rather than enumerating the ways a model might introduce itself, this takes
    everything between the FIRST `\\documentclass` and the LAST `\\end{document}`.
    Prose before, commentary after, and fences either side all fall away, and no
    new phrasing can defeat it. Idempotent.
    """
    out = (out or "").strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[-1] if "\n" in out else out
        if out.endswith("```"):
            out = out[: out.rfind("```")]
    start = out.find(_OPEN)
    if start > 0:
        out = out[start:]
    end = out.rfind(_CLOSE)
    if end != -1:
        out = out[: end + len(_CLOSE)]
    return out.strip()


def _is_complete_document(tex: str) -> bool:
    """Both markers present — checked the SAME way, deliberately.

    The shipped gate used `startswith` for the opening and `in` for the closing.
    That asymmetry was the entire bug: a symmetric check would have accepted the
    model's output by accident. `_clean` has already discarded anything outside the
    two markers, so membership is the honest test.
    """
    return _OPEN in tex and _CLOSE in tex


def latexify_node(state: ResumeState) -> ResumeState:
    if state.get("error"):
        return {}

    master_latex = state.get("master_latex", "")
    if not master_latex:
        return {}  # no template to tailor; Markdown draft stands alone

    job = state.get("job") or {}
    warnings = list(state.get("warnings", []))

    def fall_back(reason: str) -> ResumeState:
        warnings.append(
            f"Couldn't tailor the LaTeX — {reason}. Used your master résumé "
            "format untailored for the PDF."
        )
        return {"latex": master_latex, "warnings": warnings}

    # Three distinct failures, three distinct messages. They used to share one that
    # named only `type(exc).__name__`, so a parse failure was reported as
    # "CompileError" — pointing the reader at LaTeX validity when the LaTeX was
    # fine and had never been compiled. That is what kept this bug invisible.
    try:
        raw = llm(
            "local",
            _prompt(job, state.get("keywords", []), master_latex, state.get("company_research", "")),
            system=_SYSTEM,
            temperature=0.2,
            max_tokens=_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 — model unavailable / transport error
        return fall_back(f"the model call failed ({type(exc).__name__}: {exc})")

    tailored = _clean(raw)
    if not _is_complete_document(tailored):
        return fall_back(
            f"the model did not return a complete LaTeX document "
            f"(got {len(tailored)} chars, no {_OPEN}/{_CLOSE} pair)"
        )

    try:
        compile_tex(tailored)  # verify it actually builds
    except Exception as exc:  # noqa: BLE001 — bad LaTeX, missing engine, timeout
        # Carry the engine's own complaint. Without it the next silent fallback is
        # as undiagnosable as this one was.
        detail = " ".join(str(exc).split())[:240]
        return fall_back(f"the tailored source did not compile ({detail})")

    return {"latex": tailored}
