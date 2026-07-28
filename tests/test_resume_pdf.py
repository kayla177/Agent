"""Résumé PDF cache tests.

Compiling is only ~2s, so this cache is about PROVENANCE, not speed: the key is
content-versioned and an application pins the key it used, so "what exactly did
I send Stripe?" stays answerable after the résumé is edited.
"""

from __future__ import annotations

from server import resume_pdf


def test_cache_key_is_filesystem_safe():
    key = resume_pdf.cache_key("Databricks:greenhouse:7586263002", "2026-07-25T10:00:00")
    assert ":" not in key
    assert "/" not in key
    assert "Databricks" in key


def test_cache_key_changes_with_content_version():
    a = resume_pdf.cache_key("j", "2026-07-25T10:00:00")
    b = resume_pdf.cache_key("j", "2026-07-26T10:00:00")
    assert a != b, "editing a résumé must produce a new key, preserving the old file"


def test_master_key_is_stable_and_distinct():
    assert resume_pdf.cache_key(None, "2026-07-25T10:00:00").startswith("master")


def test_resume_without_latex_gets_the_MASTER_key(temp_db, monkeypatch, tmp_path):
    """A résumé with no tailored LaTeX falls back to the master's content, so it
    must also get the master's KEY. Pinning a job-specific key would claim a
    tailored résumé was sent when the generic master actually was — and the
    pinned key exists precisely to answer 'what did this company receive?'."""
    from agents.resume_generator import store as rstore

    monkeypatch.setattr(resume_pdf, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(resume_pdf, "compile_tex", lambda tex: b"%PDF-1.5 fake")

    rstore.upsert_master_resume(None, latex="\\documentclass{article}\\begin{document}M\\end{document}")
    rstore.upsert_resume("Acme:greenhouse:1", company="Acme", role="SWE Intern",
                         markdown="md", latex="", keywords=[], status="draft")

    master_key, _ = resume_pdf.ensure_pdf(None)
    fallback_key, _ = resume_pdf.ensure_pdf("Acme:greenhouse:1")
    assert fallback_key == master_key, "fallback content must carry the master's key"
    assert fallback_key.startswith("master")


def test_resume_with_its_own_latex_keeps_a_job_specific_key(temp_db, monkeypatch, tmp_path):
    from agents.resume_generator import store as rstore

    monkeypatch.setattr(resume_pdf, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(resume_pdf, "compile_tex", lambda tex: b"%PDF-1.5 fake")

    rstore.upsert_master_resume(None, latex="\\documentclass{article}\\begin{document}M\\end{document}")
    rstore.upsert_resume("Acme:greenhouse:1", company="Acme", role="SWE Intern",
                         markdown="md", keywords=[], status="draft",
                         latex="\\documentclass{article}\\begin{document}TAILORED\\end{document}")

    key, _ = resume_pdf.ensure_pdf("Acme:greenhouse:1")
    assert key.startswith("Acme_greenhouse_1__")
    assert not key.startswith("master")
