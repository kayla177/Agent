from __future__ import annotations

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
