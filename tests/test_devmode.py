"""Development identities must be reusable without escaping the dev-mode gate."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def choose(client, username):
    response = client.post("/api/dev/session", json={"username": username})
    assert response.status_code == 200, response.text
    assert response.json()["dev_username"] == username
    return response.json()


def client_in_mode(market, *, enabled, cookies=None):
    app = create_app(
        settings=Settings(devmode=enabled, allowed_origins=("http://testserver",)),
        database=market.database,
    )
    client = market.stack.enter_context(TestClient(app))
    if cookies:
        client.cookies.update(cookies)
    return client


def test_devmode_defaults_off_and_does_not_create_accounts(market, monkeypatch):
    monkeypatch.delenv("DEVMODE", raising=False)
    assert Settings().devmode is False
    client = market.client()
    assert client.get("/api/config").json()["devmode"] is False
    assert client.get("/api/dev/students").status_code == 404
    assert client.post("/api/dev/session", json={"username": "dev_student1"}).status_code == 404
    assert market.database.users.count_documents({}) == 0
    assert market.database.listings.count_documents({}) == 0


def test_named_profiles_are_reusable_and_rotate_only_the_current_cookie(dev_market):
    client = dev_market.client()
    assert client.get("/api/config").json()["devmode"] is True
    students = client.get("/api/dev/students").json()["items"]
    assert {student["username"] for student in students} == {"dev_student1", "dev_student2"}
    first = choose(client, "dev_student1")
    assert first["email_verified"] is False
    assert first["edu_eligible"] is True
    assert first["id"] == next(student["id"] for student in students if student["username"] == "dev_student1")
    old_cookie = client.cookies.get("campus_session")
    independent = dev_market.client()
    assert choose(independent, "dev_student1")["id"] == first["id"]
    second = choose(client, "dev_student2")
    assert second["id"] != first["id"]
    assert client.get("/api/me").json()["id"] == second["id"]
    stale = dev_market.client()
    stale.cookies.set("campus_session", old_cookie)
    assert stale.get("/api/me").status_code == 401
    assert independent.get("/api/me").json()["id"] == first["id"]
    assert choose(client, "dev_student1")["id"] == first["id"]
    assert dev_market.database.users.count_documents({}) == 2
    assert client.delete("/api/student/session").status_code == 204
    assert choose(client, "dev_student1")["id"] == first["id"]


def test_development_profiles_can_buy_and_sell_without_ownership_bypass(dev_market):
    first_client, second_client = dev_market.client(), dev_market.client()
    first = choose(first_client, "dev_student1")
    second = choose(second_client, "dev_student2")
    first_listing = first_client.get("/api/me/listings").json()["items"][0]
    second_listing = second_client.get("/api/me/listings").json()["items"][0]
    assert first_listing["seller"]["id"] == first["id"]
    assert second_listing["seller"]["id"] == second["id"]
    assert first_listing["is_demo"] is False and second_listing["is_demo"] is False
    buying = first_client.post(f"/api/listings/{second_listing['id']}/conversations", json={"message": "Can I buy your item?"})
    selling = second_client.post(f"/api/listings/{first_listing['id']}/conversations", json={"message": "I can collect yours tomorrow."})
    assert buying.status_code == selling.status_code == 201
    assert buying.json()["buyer"]["id"] == first["id"]
    assert selling.json()["seller"]["id"] == first["id"]
    path = f"/api/conversations/{buying.json()['id']}/messages"
    assert second_client.post(path, json={"body": "Yes, let's meet at the library."}).status_code == 201
    assert first_client.get(path).json()["total"] == 2
    assert second_client.patch(f"/api/listings/{first_listing['id']}", json={"price_cents": 1}).status_code in {403, 404}
    assert second_client.delete(f"/api/listings/{first_listing['id']}").status_code in {403, 404}
    assert first_client.patch(f"/api/listings/{first_listing['id']}", json={"status": "reserved"}).status_code == 200
    choose(first_client, "dev_student2")
    assert first_client.get("/api/me/listings").json()["items"][0]["id"] == second_listing["id"]
    choose(first_client, "dev_student1")
    assert first_client.get("/api/conversations").json()["total"] == 2
    assert first_client.get(path).json()["total"] == 2


def test_dev_seed_keeps_edits_deletions_and_new_listings_across_restart(dev_market):
    client = dev_market.client()
    choose(client, "dev_student1")
    first_listing = client.get("/api/me/listings").json()["items"][0]
    changed = client.patch(f"/api/listings/{first_listing['id']}", json={"title": "Edited sample lamp", "price_cents": 777, "status": "sold"})
    assert changed.status_code == 200
    added = dev_market.listing(client, title="New development listing")
    choose(client, "dev_student2")
    second_listing = client.get("/api/me/listings").json()["items"][0]
    assert client.delete(f"/api/listings/{second_listing['id']}").status_code == 204
    restarted = client_in_mode(dev_market, enabled=True)
    choose(restarted, "dev_student1")
    saved = restarted.get(f"/api/listings/{first_listing['id']}").json()
    assert (saved["title"], saved["price_cents"], saved["status"]) == ("Edited sample lamp", 777, "sold")
    assert restarted.get(f"/api/listings/{added['id']}").status_code == 200
    assert restarted.get(f"/api/listings/{second_listing['id']}").status_code == 404
    assert dev_market.database.users.count_documents({}) == 2
    assert dev_market.database.listings.count_documents({}) == 3


def test_dev_switch_cannot_select_arbitrary_users_or_accept_extra_identity_fields(dev_market):
    normal, profile = dev_market.profile("regular@mit.edu")
    for username in ("dev_student3", profile["id"], profile["email"], "admin", ""):
        response = normal.post("/api/dev/session", json={"username": username})
        assert response.status_code in {404, 422}, response.text
        assert normal.get("/api/me").json()["id"] == profile["id"]
    response = normal.post("/api/dev/session", json={"username": "dev_student1", "user_id": profile["id"]})
    assert response.status_code == 422
    assert normal.post("/api/dev/session", json={"username": "dev_student1"}, headers={"Origin": "https://untrusted.example"}).status_code == 403


def test_normal_email_check_does_not_recover_or_mutate_shared_dev_profile(dev_market):
    dev_client = dev_market.client()
    dev_user = choose(dev_client, "dev_student1")
    original = dev_market.database.users.find_one({"_id": dev_user["id"]})
    normal, normal_user = dev_market.profile(dev_user["email"], "Separate Student")
    assert normal_user["id"] != dev_user["id"]
    assert normal_user["dev_username"] is None
    assert normal.get("/api/me/listings").json()["total"] == 0
    old_cookie = dev_client.cookies.get("campus_session")
    response = dev_client.post("/api/student/verify", json={"email": "new@stanford.edu", "display_name": "Independent Student"})
    assert response.status_code == 200
    assert response.json()["dev_username"] is None
    assert response.json()["id"] not in {dev_user["id"], normal_user["id"]}
    assert dev_market.database.users.find_one({"_id": dev_user["id"]}) == original
    stale = dev_market.client()
    stale.cookies.set("campus_session", old_cookie)
    assert stale.get("/api/me").status_code == 401
    assert choose(dev_client, "dev_student1")["id"] == dev_user["id"]


def test_disabling_devmode_hides_dev_profiles_listings_and_conversations(dev_market):
    dev_client = dev_market.client()
    dev_user = choose(dev_client, "dev_student1")
    seeded_ids = {item["id"] for item in dev_client.get("/api/listings").json()["items"]}
    dev_listing = dev_market.listing(dev_client, title="Development-only listing")
    normal, normal_user = dev_market.profile("normal@mit.edu")
    normal_listing = dev_market.listing(normal, title="Ordinary student listing")
    second_normal, _ = dev_market.profile("another@stanford.edu")
    ordinary = second_normal.post(f"/api/listings/{normal_listing['id']}/conversations", json={"message": "I am a regular buyer."})
    assert ordinary.status_code == 201
    dev_owned = normal.post(f"/api/listings/{dev_listing['id']}/conversations", json={"message": "Can I collect this?"})
    dev_buyer = dev_client.post(f"/api/listings/{normal_listing['id']}/conversations", json={"message": "I'm testing a dev buyer."})
    assert dev_owned.status_code == dev_buyer.status_code == 201
    dev_cookies, normal_cookies = dict(dev_client.cookies), dict(normal.cookies)

    disabled_dev = client_in_mode(dev_market, enabled=False, cookies=dev_cookies)
    disabled_normal = client_in_mode(dev_market, enabled=False, cookies=normal_cookies)
    assert disabled_dev.get("/api/config").json()["devmode"] is False
    assert disabled_dev.get("/api/dev/students").status_code == 404
    assert disabled_dev.post("/api/dev/session", json={"username": "dev_student1"}).status_code == 404
    assert disabled_dev.get("/api/me").status_code == 401
    assert disabled_dev.patch(f"/api/listings/{dev_listing['id']}", json={"status": "sold"}).status_code == 401
    assert disabled_normal.get("/api/me").json()["id"] == normal_user["id"]
    assert {item["id"] for item in disabled_normal.get("/api/listings").json()["items"]} == {normal_listing["id"]}
    for listing_id in seeded_ids | {dev_listing["id"]}:
        assert disabled_normal.get(f"/api/listings/{listing_id}").status_code == 404
    assert {item["id"] for item in disabled_normal.get("/api/conversations").json()["items"]} == {ordinary.json()["id"]}
    for conversation in (dev_owned.json(), dev_buyer.json()):
        path = f"/api/conversations/{conversation['id']}/messages"
        assert disabled_normal.get(path).status_code == 404
        assert disabled_normal.post(path, json={"body": "Cannot continue while dev mode is off."}).status_code == 404
    assert disabled_normal.get(f"/api/conversations/{ordinary.json()['id']}/messages").status_code == 200
    assert dev_market.database.users.find_one({"_id": dev_user["id"]}) is not None
    assert dev_market.database.listings.find_one({"_id": dev_listing["id"]}) is not None


def test_dev_like_email_keeps_ordinary_listing_visible_when_flag_is_off(dev_market):
    client = dev_market.client()
    dev_user = choose(client, "dev_student2")
    ordinary, ordinary_user = dev_market.profile(dev_user["email"])
    listing = dev_market.listing(ordinary, title="Normal listing sharing a sample email")
    disabled = client_in_mode(dev_market, enabled=False, cookies=dict(ordinary.cookies))
    assert disabled.get("/api/me").json()["id"] == ordinary_user["id"]
    assert disabled.get(f"/api/listings/{listing['id']}").status_code == 200
    assert disabled.patch(f"/api/listings/{listing['id']}", json={"price_cents": 1500}).status_code == 200
