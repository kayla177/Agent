from __future__ import annotations

import store_db

import profile_store


def test_get_profile_empty_default(temp_db):
    p = profile_store.get_profile()
    assert p["full_name"] == ""
    assert p["needs_sponsorship"] == 0


def test_upsert_is_partial_and_single_row(temp_db):
    profile_store.upsert_profile(full_name="Kayla Li", email="k@example.com")
    profile_store.upsert_profile(phone="555-0100")
    p = profile_store.get_profile()
    assert p["full_name"] == "Kayla Li"      # preserved by the second call
    assert p["email"] == "k@example.com"
    assert p["phone"] == "555-0100"
    assert p["id"] == 1
    assert p["updated_at"]


def test_upsert_rejects_unknown_field(temp_db):
    try:
        profile_store.upsert_profile(nickname="oops")
    except ValueError as exc:
        assert "nickname" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unknown field")


def test_profile_api_roundtrip(client):
    assert client.get("/data/profile").json()["profile"]["full_name"] == ""
    res = client.put("/data/profile", json={"full_name": "Kayla Li", "summary": "CS undergrad"})
    assert res.status_code == 200
    assert res.json()["profile"]["full_name"] == "Kayla Li"
    assert client.get("/data/profile").json()["profile"]["summary"] == "CS undergrad"


def test_profile_api_rejects_empty_body(client):
    assert client.put("/data/profile", json={}).status_code == 400


# --- fit_profile_text(): the function Task 7's job fit scoring calls -------


def test_fit_profile_text_empty_when_unset(temp_db):
    assert profile_store.fit_profile_text() == ""


def test_fit_profile_text_returns_summary_once_set(temp_db):
    profile_store.upsert_profile(summary="CS undergrad, strong Python background")
    assert profile_store.fit_profile_text() == "CS undergrad, strong Python background"


def test_fit_profile_text_strips_surrounding_whitespace(temp_db):
    profile_store.upsert_profile(summary="   CS undergrad with whitespace padding   ")
    assert profile_store.fit_profile_text() == "CS undergrad with whitespace padding"


def test_fit_profile_text_missing_table_returns_empty(temp_db):
    # Simulate a DB that predates Task 4's migration (no applicant_profile table)
    # rather than raising, so a job-fit run never dies on it.
    with store_db.connect() as conn:
        conn.execute("DROP TABLE applicant_profile")
    assert profile_store.fit_profile_text() == ""


# --- work-authorization allowlist enforcement, through the HTTP layer -----


def test_profile_api_rejects_invalid_us_work_auth(client):
    res = client.put("/data/profile", json={"us_work_auth": "bogus"})
    assert res.status_code == 400


def test_profile_api_rejects_invalid_ca_work_auth(client):
    res = client.put("/data/profile", json={"ca_work_auth": "bogus"})
    assert res.status_code == 400


def test_profile_api_accepts_valid_work_auth(client):
    res = client.put("/data/profile", json={"us_work_auth": "citizen"})
    assert res.status_code == 200
    assert res.json()["profile"]["us_work_auth"] == "citizen"


# --- needs_sponsorship bool round-trips through the API (relies on the ------
# --- `is not None` filter in put_profile, not a truthiness filter) ---------


def test_profile_api_needs_sponsorship_bool_roundtrip(client):
    res_true = client.put("/data/profile", json={"needs_sponsorship": True})
    assert res_true.status_code == 200
    assert res_true.json()["profile"]["needs_sponsorship"] == 1

    res_false = client.put("/data/profile", json={"needs_sponsorship": False})
    assert res_false.status_code == 200
    assert res_false.json()["profile"]["needs_sponsorship"] == 0
