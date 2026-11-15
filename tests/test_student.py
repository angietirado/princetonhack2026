from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest


def disable_ttl_cleanup(collection):
    # mongomock 4.3 drop_indexes() retains its private TTL registry. Individual
    # drop_index calls work in both mongomock and real MongoDB.
    for name, details in collection.index_information().items():
        if "expireAfterSeconds" in details:
            collection.drop_index(name)


def test_student_domain_check_creates_browser_profile_without_email_verification(market):
    client = market.client()
    response = client.post(
        "/api/student/verify",
        json={"email": "Student@Princeton.EDU", "display_name": "Morgan"},
    )
    assert response.status_code == 200, response.text
    user = response.json()
    assert user["email"] == "student@princeton.edu"
    assert user["display_name"] == "Morgan"
    assert user["edu_eligible"] is True
    assert user["email_verified"] is False
    assert "verified" not in user
    assert client.get("/api/me").json()["id"] == user["id"]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    token = client.cookies.get("campus_session")
    assert token and token not in str(list(market.database.sessions.find()))
    assert market.database.verification_codes.count_documents({}) == 0
    assert client.get("/api/config").json()["verification_mode"] == "domain_only"


@pytest.mark.parametrize("email", [
    "student@gmail.com", "student@university.edu.evil.com", "student@notedu.com",
    "student@edu", "invalid-address", "student@university.edu\nBcc: stranger@example.com",
])
def test_non_student_and_lookalike_email_rejected(market, email):
    client = market.client()
    response = client.post("/api/student/verify", json={"email": email})
    assert response.status_code == 422, response.text
    assert client.get("/api/me").status_code == 401
    assert market.database.users.count_documents({}) == 0


@pytest.mark.parametrize("email", ["student@mit.edu", "student@cs.stanford.edu"])
def test_any_edu_including_school_subdomains_is_eligible(market, email):
    client, user = market.profile(email)
    assert user["email"] == email and client.get("/api/me").status_code == 200


def test_same_browser_can_update_profile_without_losing_its_listings(market):
    client, original = market.profile("student@mit.edu", "Original")
    listing = market.listing(client)
    old_token = client.cookies.get("campus_session")
    response = client.post("/api/student/verify", json={"email": "new@stanford.edu", "display_name": "Updated"})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == original["id"]
    assert response.json()["email"] == "new@stanford.edu"
    assert response.json()["display_name"] == "Updated"
    assert client.get("/api/me/listings").json()["items"][0]["id"] == listing["id"]
    assert client.cookies.get("campus_session") != old_token
    old_session = market.client()
    old_session.cookies.set("campus_session", old_token)
    assert old_session.get("/api/me").status_code == 401


def test_reentering_email_in_another_browser_cannot_recover_existing_profile(market):
    original, original_user = market.profile("same@mit.edu", "Original")
    buyer, _ = market.profile("buyer@stanford.edu")
    listing = market.listing(original)
    conversation = buyer.post(f"/api/listings/{listing['id']}/conversations", json={"message": "Can we meet at noon?"}).json()
    newcomer, newcomer_user = market.profile("SAME@MIT.EDU", "New browser")
    assert newcomer_user["id"] != original_user["id"]
    assert original.get("/api/me").json()["display_name"] == "Original"
    assert newcomer.get("/api/me/listings").json()["total"] == 0
    assert newcomer.get("/api/conversations").json()["total"] == 0
    assert newcomer.patch(f"/api/listings/{listing['id']}", json={"price_cents": 1}).status_code in {403, 404}
    messages_path = f"/api/conversations/{conversation['id']}/messages"
    assert newcomer.get(messages_path).status_code in {403, 404}
    assert newcomer.post(messages_path, json={"body": "Intrusion"}).status_code in {403, 404}


def test_expired_session_and_clear_profile_invalidate_access(market):
    client, original = market.profile("student@mit.edu")
    token = client.cookies.get("campus_session")
    assert client.delete("/api/student/session").status_code == 204
    assert client.get("/api/me").status_code == 401
    client.cookies.set("campus_session", token)
    assert client.get("/api/me").status_code == 401
    fresh = client.post("/api/student/verify", json={"email": "student@mit.edu"})
    assert fresh.status_code == 200 and fresh.json()["id"] != original["id"]

    disable_ttl_cleanup(market.database.sessions)
    market.database.sessions.update_many({}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert market.database.sessions.count_documents({}) == 1
    assert client.get("/api/me").status_code == 401
    after_expiry = client.post("/api/student/verify", json={"email": "student@mit.edu"})
    assert after_expiry.status_code == 200 and after_expiry.json()["id"] != fresh.json()["id"]


def test_profile_changes_require_browser_profile_and_validate_name(market):
    anonymous = market.client()
    assert anonymous.patch("/api/me", json={"display_name": "Hacker"}).status_code == 401
    client, user = market.profile("student@mit.edu")
    response = client.patch("/api/me", json={"display_name": "Jamie Chen"})
    assert response.status_code == 200
    assert response.json()["id"] == user["id"]
    assert client.get("/api/me").json()["display_name"] == "Jamie Chen"
    assert client.patch("/api/me", json={"display_name": "   "}).status_code == 422


def test_cross_origin_mutations_are_rejected(market):
    client, _ = market.profile("student@mit.edu")
    response = client.patch("/api/me", json={"display_name": "Changed"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.get("/api/me").json()["display_name"] == "Test Student"
    response = client.patch("/api/me", json={"display_name": "Allowed"}, headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert client.delete("/api/student/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/me").status_code == 200


def test_student_check_rate_limit(market):
    market.app.state.settings.student_check_limit_per_hour = 1
    first, _ = market.profile("one@mit.edu")
    response = first.post("/api/student/verify", json={"email": "two@mit.edu"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_legacy_sign_in_routes_are_removed(market):
    client = market.client()
    for path in ("/api/auth/request-code", "/api/auth/verify-code", "/api/auth/logout"):
        assert client.post(path, json={"email": "student@mit.edu", "code": "000000"}).status_code == 404


def test_old_unique_email_index_migrates_without_deleting_profiles(market):
    market.database.users.create_index("email", unique=True)
    market.database.users.insert_one({"_id": "legacy-profile", "email": "same@mit.edu", "display_name": "Legacy"})
    first, first_user = market.profile("same@mit.edu")
    second, second_user = market.profile("same@mit.edu")
    assert first_user["id"] != second_user["id"]
    assert market.database.users.find_one({"_id": "legacy-profile"})["display_name"] == "Legacy"
    assert first.get("/api/me").json()["id"] != "legacy-profile"
    assert second.get("/api/me").json()["id"] != "legacy-profile"


def test_simultaneous_same_email_checks_create_isolated_browser_profiles(market):
    clients = [market.client(), market.client()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda client: client.post("/api/student/verify", json={"email": "same@mit.edu"}), clients))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 2
    assert market.database.users.count_documents({"email": "same@mit.edu"}) == 2
