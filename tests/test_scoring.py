"""Deterministic fit-baseline tests.

The baseline exists so fit_score is NEVER null: the 8B model returns a null
score for roughly 60% of postings even with a profile set, and nulls sink to the
bottom of sort-by-fit, hiding good roles.
"""

from __future__ import annotations

from agents.job_scraper.scoring import extract_keywords, is_baseline_reason, score_baseline


def test_extract_keywords_pulls_skill_terms():
    kws = extract_keywords("3rd-year CS undergrad. Python, TypeScript, React, SQL and LangGraph.")
    assert "python" in kws
    assert "typescript" in kws
    assert "react" in kws
    # Stopwords and filler must not become keywords.
    assert "and" not in kws
    assert "3rd" not in kws


def test_score_rises_with_overlap():
    kws = ["python", "react", "sql"]
    none_matched = score_baseline(kws, {"title": "Chef Intern", "description": "Cook food."})[0]
    one_matched = score_baseline(kws, {"title": "Intern", "description": "You will use Python."})[0]
    all_matched = score_baseline(kws, {"title": "SWE Intern", "description": "Python, React, SQL."})[0]
    assert none_matched < one_matched < all_matched
    assert 0 <= none_matched and all_matched <= 100


def test_reason_names_the_matches():
    _, reason = score_baseline(["python", "sql"], {"title": "X", "description": "Python and SQL work"})
    assert "Python" in reason or "python" in reason
    assert is_baseline_reason(reason)


def test_no_keywords_gives_neutral_score_and_marked_reason():
    score, reason = score_baseline([], {"title": "SWE Intern", "description": "anything"})
    assert score == 50
    assert is_baseline_reason(reason)


def test_word_boundary_no_false_positive():
    """'r' must not match inside 'Research'; 'go' must not match 'Going'.

    Both postings share the SAME title so the target-role title bonus is held
    constant and only the description varies — otherwise this compares 35 to 25
    and fails for a reason that has nothing to do with word boundaries.
    """
    kws = ["go", "r"]
    tricky = score_baseline(kws, {"title": "Research Intern", "description": "Going deep."})[0]
    clean = score_baseline(kws, {"title": "Research Intern", "description": "Nothing here."})[0]
    assert tricky == clean


def test_target_role_title_bonus():
    kws = ["python"]
    intern = score_baseline(kws, {"title": "Software Engineer Intern", "description": "Python"})[0]
    unclear = score_baseline(kws, {"title": "Software Engineer II", "description": "Python"})[0]
    assert intern > unclear


def test_llm_reason_is_not_baseline():
    assert not is_baseline_reason("Strong Python background, great fit for the team")
