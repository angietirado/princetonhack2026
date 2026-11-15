"""Persisted flows required by the partner's 2ndLife interface."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from test_devmode import choose, client_in_mode


def order(client, listing, **changes):
    payload = {"message": "Can we arrange pickup at the library?"}
    payload.update(changes)
    response = client.post(f"/api/listings/{listing['id']}/orders", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def advance(client, current, status, **changes):
    response = client.patch(f"/api/orders/{current['id']}", json={"status": status, **changes})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == status
    return response.json()


def test_listing_kinds_location_search_and_legacy_documents(market):
    seller, _ = market.profile("seller@mit.edu")
    legacy = market.listing(seller, title="Legacy desk lamp", category="electronics")
    market.database.listings.update_one(
        {"_id": legacy["id"]},
        {"$unset": {"kind": "", "needs_repair": "", "rental_period": ""}},
    )
    rental = market.listing(seller, title="Calculator rental", kind="rent", price_cents=200, pickup_location="GITC")
    free = market.listing(seller, title="Free packing tape", kind="free", price_cents=0, pickup_location="GITC")
    urgent = market.listing(seller, title="Packing tape needed", kind="urgent", price_cents=0, pickup_location="Campus Center")
    wanted = market.listing(seller, title="Wanted desk fan", kind="wanted", price_cents=0, needs_repair=False)
    reader = market.client()
    saved = reader.get(f"/api/listings/{legacy['id']}").json()
    assert saved["kind"] == "sale" and saved["needs_repair"] is False
    assert rental["rental_period"] == "day"
    assert {item["id"] for item in reader.get("/api/listings", params={"kind": "sale"}).json()["items"]} == {legacy["id"]}
    for kind, listing in (("rent", rental), ("free", free), ("urgent", urgent), ("wanted", wanted)):
        response = reader.get("/api/listings", params={"kind": kind})
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [listing["id"]]
    narrowed = reader.get("/api/listings", params={"location": "GITC", "q": "tape"}).json()
    assert [item["id"] for item in narrowed["items"]] == [free["id"]]
    assert reader.get("/api/listings", params={"kind": "sale", "q": "no matching phrase"}).json()["total"] == 0
    assert reader.get("/api/listings", params={"location": ".*"}).json()["total"] == 0


@pytest.mark.parametrize("changes", [
    {"kind": "unknown"},
    {"kind": "free", "price_cents": 100},
    {"kind": "urgent", "price_cents": 100},
    {"kind": "wanted", "price_cents": 100},
    {"kind": "rent", "price_cents": 0},
])
def test_listing_kind_pricing_rejects_invalid_combinations(market, changes):
    seller, _ = market.profile("seller@mit.edu")
    payload = {
        "title": "Student listing", "description": "A useful item for another student.",
        "price_cents": 500, "category": "other", "condition": "good", "pickup_location": "Library",
        **changes,
    }
    response = seller.post("/api/listings", json=payload)
    assert response.status_code == 422, response.text
    assert seller.get("/api/me/listings").json()["total"] == 0


def test_profile_location_and_free_tickets_survive_reload_without_edit_farming(market):
    seller, student = market.profile("seller@mit.edu")
    assert student["iou_tickets"] == 0
    response = seller.patch("/api/me", json={"campus_location": "Maple Hall"})
    assert response.status_code == 200, response.text
    assert seller.get("/api/me").json()["campus_location"] == "Maple Hall"
    free = market.listing(seller, kind="free", price_cents=0)
    assert seller.get("/api/me").json()["iou_tickets"] == 1
    for title in ("Free study notes", "Updated free notes"):
        assert seller.patch(f"/api/listings/{free['id']}", json={"title": title}).status_code == 200
        assert seller.get("/api/me").json()["iou_tickets"] == 1
    assert seller.delete(f"/api/listings/{free['id']}").status_code == 204
    restarted = client_in_mode(market, enabled=False, cookies=dict(seller.cookies))
    profile = restarted.get("/api/me").json()
    assert profile["iou_tickets"] == 1 and profile["campus_location"] == "Maple Hall"
    assert restarted.patch("/api/me", json={"iou_tickets": 100}).status_code == 422
    public = market.listing(seller)
    assert "iou_tickets" not in public["seller"]
    assert "campus_location" not in public["seller"]


def test_switching_listing_kind_cannot_mint_free_tickets(market):
    seller, _ = market.profile("seller@mit.edu")
    listing = market.listing(seller)
    for _ in range(2):
        response = seller.patch(f"/api/listings/{listing['id']}", json={"kind": "free", "price_cents": 0})
        assert response.status_code == 200, response.text
        assert seller.get("/api/me").json()["iou_tickets"] == 0
        assert seller.patch(f"/api/listings/{listing['id']}", json={"kind": "sale", "price_cents": 100}).status_code == 200
    free = market.listing(seller, kind="free", price_cents=0)
    assert seller.get("/api/me").json()["iou_tickets"] == 1
    assert seller.patch(f"/api/listings/{free['id']}", json={"kind": "sale", "price_cents": 100}).status_code == 200
    assert seller.patch(f"/api/listings/{free['id']}", json={"kind": "free", "price_cents": 0}).status_code == 200
    assert seller.get("/api/me").json()["iou_tickets"] == 1


def test_order_reuses_chat_and_is_private_idempotent_and_role_filtered(market):
    seller, seller_user = market.profile("seller@mit.edu", "Seller")
    buyer, buyer_user = market.profile("buyer@stanford.edu", "Buyer")
    stranger, _ = market.profile("stranger@princeton.edu")
    listing = market.listing(seller)
    path = f"/api/listings/{listing['id']}/orders"
    assert market.client().post(path, json={}).status_code == 401
    assert seller.post(path, json={}).status_code in {400, 403, 409}
    conversation = buyer.post(f"/api/listings/{listing['id']}/conversations", json={"message": "Is it available?"}).json()
    created = order(buyer, listing)
    assert created["status"] == "requested"
    assert created["conversation_id"] == conversation["id"]
    assert created["buyer"]["id"] == buyer_user["id"]
    assert created["seller"]["id"] == seller_user["id"]
    messages_path = f"/api/conversations/{conversation['id']}/messages"
    messages = buyer.get(messages_path).json()["items"]
    assert [message["body"] for message in messages] == ["Is it available?", "Can we arrange pickup at the library?"]
    assert buyer.get("/api/conversations").json()["items"][0]["last_message"] == "Can we arrange pickup at the library?"
    repeated = buyer.post(path, json={"message": "Repeated click"})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == created["id"]
    assert buyer.get(messages_path).json()["total"] == 2
    assert buyer.get("/api/conversations").json()["total"] == 1
    for role, client in (("buying", buyer), ("selling", seller)):
        assert [item["id"] for item in client.get("/api/orders", params={"role": role}).json()["items"]] == [created["id"]]
    assert buyer.get("/api/orders", params={"role": "selling"}).json()["total"] == 0
    assert buyer.get("/api/orders", params={"role": "borrowing"}).json()["total"] == 0
    assert stranger.get("/api/orders").json()["total"] == 0
    assert stranger.get(f"/api/orders/{created['id']}").status_code == 404
    assert stranger.patch(f"/api/orders/{created['id']}", json={"status": "accepted"}).status_code == 404
    assert "email" not in created["buyer"] and "email" not in created["seller"]


def test_sale_pickup_lifecycle_and_competing_requests(market):
    seller, _ = market.profile("seller@mit.edu")
    buyer, _ = market.profile("buyer@stanford.edu")
    other_buyer, _ = market.profile("other@princeton.edu")
    listing = market.listing(seller)
    first, second = order(buyer, listing), order(other_buyer, listing)
    path = f"/api/orders/{first['id']}"
    assert buyer.patch(path, json={"status": "accepted"}).status_code in {403, 409}
    assert seller.patch(path, json={"status": "completed"}).status_code in {400, 409, 422}
    advance(seller, first, "accepted", pickup_location="Campus Center desk")
    assert buyer.get(path).json()["pickup_location"] == "Campus Center desk"
    assert buyer.patch(path, json={"pickup_location": "Another pickup desk"}).status_code in {403, 409}
    assert seller.patch(path, json={"pickup_location": "Library front desk"}).status_code == 200
    assert buyer.get(path).json()["pickup_location"] == "Library front desk"
    assert buyer.get(f"/api/listings/{listing['id']}").json()["status"] == "reserved"
    assert seller.patch(f"/api/listings/{listing['id']}", json={"price_cents": 9999}).status_code == 409
    assert seller.delete(f"/api/listings/{listing['id']}").status_code == 409
    assert seller.patch(f"/api/orders/{second['id']}", json={"status": "accepted"}).status_code == 409
    advance(seller, first, "ready")
    advance(buyer, first, "completed")
    assert buyer.get(f"/api/listings/{listing['id']}").json()["status"] == "sold"
    assert seller.patch(f"/api/listings/{listing['id']}", json={"status": "available"}).status_code == 409
    assert seller.patch(path, json={"status": "requested"}).status_code in {400, 409, 422}
    assert seller.patch(f"/api/orders/{second['id']}", json={"status": "accepted"}).status_code == 409
    assert seller.delete(f"/api/listings/{listing['id']}").status_code == 204
    assert buyer.get(f"/api/listings/{listing['id']}").status_code == 404
    assert buyer.get(path).json()["status"] == "completed"
    assert buyer.get(path).json()["listing"]["title"] == listing["title"]
    assert buyer.get(f"/api/conversations/{first['conversation_id']}/messages").status_code == 200


@pytest.mark.parametrize("changes", [{"kind": "rent", "price_cents": 200}, {"price_cents": 9000}])
def test_pending_order_retains_terms_and_cannot_accept_changed_listing(market, changes):
    seller, _ = market.profile("seller@mit.edu")
    buyer, _ = market.profile("buyer@stanford.edu")
    listing = market.listing(seller)
    pending = order(buyer, listing)
    assert seller.patch(f"/api/listings/{listing['id']}", json=changes).status_code == 200
    saved = buyer.get(f"/api/orders/{pending['id']}").json()
    assert saved["listing"]["kind"] == "sale"
    assert saved["listing"]["price_cents"] == listing["price_cents"]
    assert seller.patch(f"/api/orders/{pending['id']}", json={"status": "accepted"}).status_code == 409
    advance(buyer, pending, "cancelled")
    replacement = order(buyer, listing)
    assert replacement["id"] != pending["id"]
    assert replacement["listing"]["price_cents"] == changes["price_cents"]
    assert replacement["listing"]["kind"] == changes.get("kind", "sale")
    advance(seller, replacement, "accepted")


def test_rental_return_reopens_listing_and_cancel_releases_reservation(market):
    owner, _ = market.profile("owner@mit.edu")
    renter, _ = market.profile("renter@stanford.edu")
    listing = market.listing(owner, kind="rent", price_cents=200)
    rental = order(renter, listing)
    assert renter.get("/api/orders", params={"role": "borrowing"}).json()["items"][0]["id"] == rental["id"]
    advance(owner, rental, "accepted")
    advance(owner, rental, "ready")
    advance(renter, rental, "completed")
    assert owner.get(f"/api/listings/{listing['id']}").json()["status"] == "reserved"
    assert renter.patch(f"/api/orders/{rental['id']}", json={"status": "returned"}).status_code in {403, 409}
    advance(owner, rental, "returned")
    assert owner.get(f"/api/listings/{listing['id']}").json()["status"] == "available"
    repeat_rental = order(renter, listing)
    assert repeat_rental["id"] != rental["id"]
    assert renter.get(f"/api/orders/{rental['id']}").json()["status"] == "returned"
    another = market.listing(owner, title="Another campus item")
    pickup = order(renter, another)
    advance(owner, pickup, "accepted")
    advance(renter, pickup, "cancelled")
    assert owner.get(f"/api/listings/{another['id']}").json()["status"] == "available"
    repeat_pickup = order(renter, another)
    assert repeat_pickup["id"] != pickup["id"]
    assert renter.get(f"/api/orders/{pickup['id']}").json()["status"] == "cancelled"


def test_simultaneous_pickup_requests_create_one_order_and_conversation(market):
    seller, _ = market.profile("seller@mit.edu")
    buyer, _ = market.profile("buyer@stanford.edu")
    listing = market.listing(seller)
    path = f"/api/listings/{listing['id']}/orders"
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: buyer.post(path, json={"message": "May I pick up today?"}), range(3)))
    assert all(response.status_code in {200, 201} for response in responses), [response.text for response in responses]
    assert sum(response.status_code == 201 for response in responses) == 1
    assert len({response.json()["id"] for response in responses}) == 1
    assert buyer.get("/api/orders").json()["total"] == 1
    assert buyer.get("/api/conversations").json()["total"] == 1
    conversation_id = responses[0].json()["conversation_id"]
    assert buyer.get(f"/api/conversations/{conversation_id}/messages").json()["total"] == 1


def test_simultaneous_acceptance_reserves_for_only_one_buyer(market):
    seller, _ = market.profile("seller@mit.edu")
    buyer, _ = market.profile("buyer@stanford.edu")
    other, _ = market.profile("other@princeton.edu")
    listing = market.listing(seller)
    requests = [order(buyer, listing), order(other, listing)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda request: seller.patch(f"/api/orders/{request['id']}", json={"status": "accepted"}), requests))
    assert sorted(response.status_code for response in responses) == [200, 409], [response.text for response in responses]
    states = seller.get("/api/orders", params={"role": "selling"}).json()["items"]
    assert sum(item["status"] == "accepted" for item in states) == 1


def test_deleted_listing_keeps_order_and_chat_history(market):
    seller, _ = market.profile("seller@mit.edu")
    buyer, _ = market.profile("buyer@stanford.edu")
    listing = market.listing(seller)
    request = order(buyer, listing)
    assert seller.delete(f"/api/listings/{listing['id']}").status_code == 204
    response = buyer.get(f"/api/orders/{request['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["listing"]["title"] == listing["title"]
    assert buyer.get(f"/api/conversations/{request['conversation_id']}/messages").status_code == 200
    assert seller.patch(f"/api/orders/{request['id']}", json={"status": "accepted"}).status_code in {404, 409}


def repair_profile(client, **changes):
    payload = {
        "specialties": ["Laptops", "Appliances"],
        "about": "I repair campus laptops and small desk appliances.",
        "rate_cents": 1500,
        "portfolio_image_url": "https://example.com/past-repair.jpg",
        **changes,
    }
    response = client.post("/api/repairers/me", json=payload)
    assert response.status_code in {200, 201}, response.text
    return response.json()


def repair_request(client, repairer, **changes):
    response = client.post(f"/api/repairers/{repairer['id']}/requests", json={
        "description": "My laptop screen flashes after starting.", "location": "GITC lobby", **changes,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_repair_profile_persists_as_self_reported_with_safe_public_fields(market):
    repairer, person = market.profile("repairer@mit.edu", "Campus Repairer")
    other, _ = market.profile("other@stanford.edu")
    profile = repair_profile(repairer)
    assert profile["user"]["id"] == person["id"]
    assert profile["verification"] == "self_reported"
    assert "email" not in profile["user"]
    updated = repair_profile(repairer, rate_cents=2000, specialties=["Bikes"])
    assert updated["id"] == profile["id"]
    assert repairer.get("/api/repairers/me").json()["rate_cents"] == 2000
    directory = market.client().get("/api/repairers").json()
    assert directory["total"] == 1
    assert directory["items"][0]["specialties"] == ["Bikes"]
    assert market.client().get("/api/repairers", params={"specialty": "bikes"}).json()["total"] == 1
    assert market.client().get("/api/repairers", params={"specialty": "Laptops"}).json()["total"] == 0
    assert other.get("/api/repairers/me").status_code == 404
    assert market.client().post("/api/repairers/me", json={}).status_code in {401, 422}
    assert repairer.post("/api/repairers/me", json={
        "specialties": [], "about": "Too short", "rate_cents": -1,
        "portfolio_image_url": "javascript:alert(1)",
    }).status_code == 422
    restarted = client_in_mode(market, enabled=False, cookies=dict(repairer.cookies))
    assert restarted.get("/api/repairers/me").json()["id"] == profile["id"]
    assert restarted.get("/api/repairers/me").json()["specialties"] == ["Bikes"]


def test_repair_request_lifecycle_and_private_chat(market):
    repairer, repairer_user = market.profile("repairer@mit.edu")
    requester, requester_user = market.profile("requester@stanford.edu")
    stranger, _ = market.profile("stranger@princeton.edu")
    profile = repair_profile(repairer)
    path = f"/api/repairers/{profile['id']}/requests"
    assert repairer.post(path, json={"description": "Please repair my laptop", "location": "Library"}).status_code in {400, 403, 409}
    request = repair_request(requester, profile)
    assert request["status"] == "requested"
    assert request["requester"]["id"] == requester_user["id"]
    assert request["repairer"]["user"]["id"] == repairer_user["id"]
    assert requester.get("/api/repair-requests").json()["items"][0]["id"] == request["id"]
    assert repairer.get("/api/repair-requests").json()["items"][0]["id"] == request["id"]
    assert stranger.get("/api/repair-requests").json()["total"] == 0
    request_path = f"/api/repair-requests/{request['id']}"
    assert stranger.get(request_path).status_code == 404
    assert stranger.patch(request_path, json={"status": "accepted"}).status_code == 404
    assert requester.patch(request_path, json={"status": "accepted"}).status_code in {403, 409}
    assert repairer.patch(request_path, json={"status": "completed"}).status_code == 409
    assert repairer.patch(request_path, json={"status": "accepted"}).status_code == 200
    messages_path = f"/api/conversations/{request['conversation_id']}/messages"
    assert stranger.get(messages_path).status_code == 404
    assert repairer.post(messages_path, json={"body": "Bring it to the library tomorrow."}).status_code == 201
    assert requester.get(messages_path).json()["items"][-1]["body"] == "Bring it to the library tomorrow."
    assert repairer.patch(request_path, json={"status": "completed"}).status_code == 200
    assert requester.patch(request_path, json={"status": "cancelled"}).status_code == 409
    assert market.client().get("/api/listings").json()["total"] == 0
    assert repairer.get("/api/me/listings").json()["total"] == 0
    service_listing_id = requester.get("/api/conversations").json()["items"][0]["listing"]["id"]
    assert market.client().get(f"/api/listings/{service_listing_id}").status_code == 404
    assert stranger.post(f"/api/listings/{service_listing_id}/orders", json={}).status_code == 404
    assert stranger.post(f"/api/listings/{service_listing_id}/conversations", json={"message": "Please expose this repair"}).status_code == 404
    assert repairer.patch(f"/api/listings/{service_listing_id}", json={"title": "Public repair listing"}).status_code == 404


def test_disabled_devmode_hides_orders_repair_profiles_requests_and_chats(dev_market):
    dev = dev_market.client()
    choose(dev, "dev_student1")
    normal, _ = dev_market.profile("normal@mit.edu")
    other, _ = dev_market.profile("other@stanford.edu")
    normal_listing = dev_market.listing(normal, title="Normal desk lamp")
    dev_listing = dev_market.listing(dev, title="Development desk lamp")
    normal_order = order(other, normal_listing)
    dev_buyer_order = order(dev, normal_listing)
    dev_seller_order = order(normal, dev_listing)
    normal_repairer = repair_profile(normal)
    dev_repairer = repair_profile(dev)
    normal_request = repair_request(other, normal_repairer)
    dev_customer_request = repair_request(dev, normal_repairer)
    dev_repairer_request = repair_request(normal, dev_repairer)
    off_normal = client_in_mode(dev_market, enabled=False, cookies=dict(normal.cookies))
    off_dev = client_in_mode(dev_market, enabled=False, cookies=dict(dev.cookies))
    assert off_dev.get("/api/orders").status_code == 401
    assert off_dev.get("/api/repair-requests").status_code == 401
    assert {item["id"] for item in off_normal.get("/api/orders").json()["items"]} == {normal_order["id"]}
    assert {item["id"] for item in off_normal.get("/api/repairers").json()["items"]} == {normal_repairer["id"]}
    assert {item["id"] for item in off_normal.get("/api/repair-requests").json()["items"]} == {normal_request["id"]}
    for request in (dev_buyer_order, dev_seller_order):
        path = f"/api/orders/{request['id']}"
        assert off_normal.get(path).status_code == 404
        assert off_normal.patch(path, json={"status": "cancelled"}).status_code == 404
        assert off_normal.get(f"/api/conversations/{request['conversation_id']}/messages").status_code == 404
    for request in (dev_customer_request, dev_repairer_request):
        assert off_normal.get(f"/api/repair-requests/{request['id']}").status_code == 404
        assert off_normal.patch(f"/api/repair-requests/{request['id']}", json={"status": "cancelled"}).status_code == 404
        assert off_normal.get(f"/api/conversations/{request['conversation_id']}/messages").status_code == 404
    assert off_normal.post(f"/api/repairers/{dev_repairer['id']}/requests", json={"description": "Please repair my laptop.", "location": "Library"}).status_code == 404
