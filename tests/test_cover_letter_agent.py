"""The cover-letter agent. Three nodes, and the refusals matter more than the
happy path: a letter with no voice to imitate could only be invented, which is
exactly what this feature must not do."""
from __future__ import annotations

from agents.cover_letter_generator import store as cl
from agents.cover_letter_generator.graph import build_cover_letter_graph
from agents.cover_letter_generator.nodes.draft import draft_node
from agents.cover_letter_generator.nodes.gather import gather_node
from agents.job_scraper import store as jobstore

JOB = {
    "id": "Acme:greenhouse:1", "company": "Acme", "ats": "greenhouse",
    "title": "Software Engineer Intern", "location": "Austin, TX",
    "description": "You will write Python and talk to customers.", "status": "new",
}


def _seed(temp_db, *, master="Dear team,\n\nI like building things.\n\nKayla"):
    jobstore.replace_record(JOB)
    if master:
        cl.upsert_master_cover_letter(master)


def test_no_job_id_is_refused_because_this_is_per_job(temp_db):
    out = gather_node({})
    assert out["error"] == "no_job"
    assert "job" in out["message"].lower()


def test_an_unknown_job_id_is_refused(temp_db):
    _seed(temp_db)
    out = gather_node({"job_id": "nobody:greenhouse:0"})
    assert out["error"] == "no_job"


def test_no_master_letter_is_refused_because_there_is_no_voice_to_imitate(temp_db):
    """The refusal that matters most. Without a sample letter the model has
    nothing to imitate, and a letter invented from nothing is the failure mode
    this whole feature is designed to avoid. The message must point her at the
    résumé tab rather than just saying no."""
    _seed(temp_db, master="")
    out = gather_node({"job_id": JOB["id"]})
    assert out["error"] == "no_master"
    assert "résumé tab" in out["message"] or "resume tab" in out["message"]


def test_a_whitespace_only_master_letter_counts_as_absent(temp_db):
    _seed(temp_db, master="   \n\t\n  ")
    assert gather_node({"job_id": JOB["id"]})["error"] == "no_master"


def test_gather_loads_the_job_the_master_and_the_profile(temp_db):
    _seed(temp_db)
    out = gather_node({"job_id": JOB["id"]})
    assert not out.get("error")
    assert out["job"]["title"] == "Software Engineer Intern"
    assert "building things" in out["master"]
    assert "profile" in out


def test_gather_passes_the_tailored_resume_along_when_one_exists(temp_db):
    """Context, not a prerequisite: the letter must not claim experience the
    résumé does not show, but a missing résumé is fine."""
    from agents.resume_generator import store as rs
    _seed(temp_db)
    rs.upsert_resume(JOB["id"], company="Acme", role="SWE Intern",
                     markdown="- Built a thing at Steelcon", keywords=[])
    out = gather_node({"job_id": JOB["id"]})
    assert "Steelcon" in out["resume_body"]


def test_gather_is_fine_with_no_resume(temp_db):
    _seed(temp_db)
    assert gather_node({"job_id": JOB["id"]})["resume_body"] == ""


def test_draft_refuses_without_ever_calling_the_model_when_gather_failed(temp_db, monkeypatch):
    """A refusal test must prove the model was NOT called, not merely that the
    output was empty. `tests/conftest.py` installs a suite-wide guard; this
    additionally records calls so the assertion is about the call list."""
    calls = []
    import agents.cover_letter_generator.nodes.draft as draft_mod
    monkeypatch.setattr(draft_mod, "llm", lambda *a, **k: calls.append(a) or "invented")
    out = draft_node({"error": "no_master"})
    assert out == {}
    assert calls == [], "a short-circuited node must not reach the model"


def test_draft_stores_what_the_model_returned(temp_db, monkeypatch):
    import agents.cover_letter_generator.nodes.draft as draft_mod
    monkeypatch.setattr(draft_mod, "llm", lambda *a, **k: "Dear Acme,\n\nI want in.\n")
    out = draft_node({"job": JOB, "master": "Dear team,", "resume_body": "", "profile": {}})
    assert out["body"] == "Dear Acme,\n\nI want in.\n"
    assert not out.get("error")


def test_an_empty_model_reply_is_an_error_not_an_empty_letter(temp_db, monkeypatch):
    import agents.cover_letter_generator.nodes.draft as draft_mod
    monkeypatch.setattr(draft_mod, "llm", lambda *a, **k: "   \n  ")
    out = draft_node({"job": JOB, "master": "Dear team,", "resume_body": "", "profile": {}})
    assert out["error"] == "draft_failed"


def test_a_model_that_raises_is_an_error_not_a_crash(temp_db, monkeypatch):
    import agents.cover_letter_generator.nodes.draft as draft_mod

    def boom(*a, **k):
        raise RuntimeError("ollama is not running")
    monkeypatch.setattr(draft_mod, "llm", boom)
    out = draft_node({"job": JOB, "master": "Dear team,", "resume_body": "", "profile": {}})
    assert out["error"] == "draft_failed"
    assert "ollama is not running" in out["message"]


def test_the_prompt_carries_the_master_letter_and_the_posting(temp_db, monkeypatch):
    """The two inputs that make this a tailored letter in her voice rather than a
    generic one."""
    seen = {}
    import agents.cover_letter_generator.nodes.draft as draft_mod

    def capture(tier, prompt, **kw):
        seen["prompt"] = prompt
        seen["system"] = kw.get("system", "")
        return "letter"
    monkeypatch.setattr(draft_mod, "llm", capture)
    draft_node({"job": JOB, "master": "MY-VOICE-MARKER", "resume_body": "RESUME-MARKER",
                "profile": {"full_name": "Kayla Li"}})
    assert "MY-VOICE-MARKER" in seen["prompt"]
    assert "Software Engineer Intern" in seen["prompt"]
    assert "RESUME-MARKER" in seen["prompt"]
    assert "never invent" in seen["system"].lower()


def test_the_whole_graph_saves_a_letter(temp_db, monkeypatch):
    import agents.cover_letter_generator.nodes.draft as draft_mod
    monkeypatch.setattr(draft_mod, "llm", lambda *a, **k: "Dear Acme,\n\nHire me.\n")
    _seed(temp_db)
    final = build_cover_letter_graph(send=False).invoke({"job_id": JOB["id"]})
    assert not final.get("error")
    stored = cl.get_cover_letter(JOB["id"])
    assert stored["body"] == "Dear Acme,\n\nHire me.\n"
    assert stored["company"] == "Acme" and stored["role"] == "Software Engineer Intern"
    assert "Acme" in final["message"]


def test_a_refused_run_saves_nothing_and_passes_its_message_out(temp_db):
    _seed(temp_db, master="")
    final = build_cover_letter_graph(send=False).invoke({"job_id": JOB["id"]})
    assert final["error"] == "no_master"
    assert cl.get_cover_letter(JOB["id"]) is None, "a refusal must not write a row"
    assert final["message"]


def test_the_agent_is_registered_on_the_resume_tab():
    from agents.registry import get_spec
    spec = get_spec("cover_letter_generator")
    assert spec.node_order == ("gather", "draft", "save")
    assert spec.planet == "jupiter" and spec.label == "resume"


def test_the_builder_accepts_and_ignores_send():
    """Same contract every registry builder has; there is no Discord delivery for
    a per-job document."""
    assert build_cover_letter_graph(send=True) is not None


# ------------------------------------------------------------------ routes
#
# These use the EXISTING `client` fixture from tests/conftest.py, not a
# hand-rolled TestClient. It shares the temp DB *and* repoints
# `server.db.DB_PATH`, which matters: `server/db.py` caches DB_PATH at import, so
# a bare TestClient(app) would let the app's startup hook
# (`mark_stale_running_as_error()`) run against the REAL data/control_center.db.


def test_the_master_letter_can_be_read_and_written_over_http(client):
    assert client.get("/data/cover-letter/master").json()["cover_letter"]["body"] == ""
    r = client.put("/data/cover-letter/master", json={"body": "Dear team,"})
    assert r.status_code == 200
    assert client.get("/data/cover-letter/master").json()["cover_letter"]["body"] == "Dear team,"


def test_versions_are_served_for_one_job(client):
    cl.upsert_cover_letter("j", company="A", role="R", body="one")
    cl.upsert_cover_letter("j", company="A", role="R", body="two")
    got = client.get("/data/cover-letters/j/versions").json()
    assert [v["body"] for v in got["versions"]] == ["one"]


def test_status_can_be_patched_and_a_bad_status_is_rejected(client):
    cl.upsert_cover_letter("j", company="A", role="R", body="x")
    assert client.patch("/data/cover-letters/j", json={"status": "final"}).status_code == 200
    assert cl.get_cover_letter("j")["status"] == "final"
    assert client.patch("/data/cover-letters/j", json={"status": "nope"}).status_code == 422


def test_patching_a_missing_letter_is_a_404_not_a_500(client):
    assert client.patch("/data/cover-letters/missing", json={"status": "final"}).status_code == 404
