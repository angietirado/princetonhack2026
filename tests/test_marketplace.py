import pytest
from concurrent.futures import ThreadPoolExecutor


def test_public_browsing_and_owner_only_listing_controls(market):
    seller, seller_user = market.profile("seller@mit.edu", "Seller")
    stranger, _ = market.profile("stranger@stanford.edu", "Stranger")
    listing = market.listing(seller)
    listing_id = listing["id"]
    anonymous = market.client()
    response = anonymous.get(f"/api/listings/{listing_id}")
    assert response.status_code == 200
    assert response.json()["seller"]["id"] == seller_user["id"]
    assert "email" not in response.json()["seller"]
    assert seller_user["email"] not in anonymous.get("/api/listings").text
    assert anonymous.post("/api/listings", json={}).status_code in {401, 422}
    assert anonymous.get("/api/me/listings").status_code == 401
    assert stranger.patch(f"/api/listings/{listing_id}", json={"price_cents": 1}).status_code in {403, 404}
    assert stranger.delete(f"/api/listings/{listing_id}").status_code in {403, 404}
    assert anonymous.get(f"/api/listings/{listing_id}").json()["price_cents"] == 2500
    changed = seller.patch(f"/api/listings/{listing_id}", json={"price_cents": 2000, "title": "Updated textbook"})
    assert changed.status_code == 200 and changed.json()["price_cents"] == 2000
    assert seller.get("/api/me/listings").json()["total"] == 1
    assert stranger.get("/api/me/listings").json()["total"] == 0


@pytest.mark.parametrize("changes", [
    {"price_cents": -1}, {"price_cents": 12.5}, {"price_cents": True},
    {"title": "   "}, {"category": "not-a-category"}, {"condition": "broken"},
    {"image_url": "javascript:alert(1)"}, {"image_url": "data:text/html,hello"},
])
def test_listing_rejects_invalid_or_unsafe_input(market, changes):
    seller, _ = market.profile("seller@mit.edu")
    payload = {
        "title": "Desk lamp", "description": "A working adjustable desk lamp for studying.",
        "price_cents": 1500, "category": "furniture", "condition": "good", "pickup_location": "Library",
    }
    payload.update(changes)
    assert seller.post("/api/listings", json=payload).status_code == 422
    assert seller.get("/api/me/listings").json()["total"] == 0


def test_listing_search_filters_pagination_and_sold_visibility(market):
    seller, _ = market.profile("seller@mit.edu")
    cheap = market.listing(seller, title="Calculus notes", price_cents=500)
    middle = market.listing(seller, title="Chemistry textbook", price_cents=1500)
    expensive = market.listing(seller, title="Adjustable desk lamp", category="furniture", price_cents=3500)
    client = market.client()
    page = client.get("/api/listings", params={"category": "books", "min_price": 1000, "max_price": 2000}).json()
    assert [item["id"] for item in page["items"]] == [middle["id"]]
    page = client.get("/api/listings", params={"q": "chemistry"}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == middle["id"]
    assert client.get("/api/listings", params={"q": ".*"}).json()["total"] == 0
    page = client.get("/api/listings", params={"sort": "price_asc", "page_size": 2}).json()
    assert page["total"] == 3 and page["page"] == 1 and page["page_size"] == 2
    assert [item["id"] for item in page["items"]] == [cheap["id"], middle["id"]]
    page = client.get("/api/listings", params={"sort": "price_asc", "page_size": 2, "page": 2}).json()
    assert [item["id"] for item in page["items"]] == [expensive["id"]]
    assert seller.patch(f"/api/listings/{middle['id']}", json={"status": "reserved"}).status_code == 200
    assert client.get("/api/listings").json()["total"] == 3
    assert seller.patch(f"/api/listings/{middle['id']}", json={"status": "sold"}).status_code == 200
    assert client.get("/api/listings").json()["total"] == 2
    assert client.get(f"/api/listings/{middle['id']}").json()["status"] == "sold"
    assert seller.get("/api/me/listings").json()["total"] == 3


def test_buyer_seller_conversation_and_soft_delete_flow(market):
    seller, seller_user = market.profile("seller@mit.edu", "Seller")
    buyer, buyer_user = market.profile("buyer@stanford.edu", "Buyer")
    stranger, _ = market.profile("stranger@princeton.edu", "Stranger")
    listing = market.listing(seller)
    path = f"/api/listings/{listing['id']}/conversations"
    assert market.client().post(path, json={"message": "Still available?"}).status_code == 401
    assert seller.post(path, json={"message": "My own listing"}).status_code in {400, 403, 409}
    response = buyer.post(path, json={"message": "Could I pick it up tomorrow?"})
    assert response.status_code == 201, response.text
    conversation = response.json()
    conversation_id = conversation["id"]
    assert conversation["buyer"]["id"] == buyer_user["id"]
    assert conversation["seller"]["id"] == seller_user["id"]
    assert "email" not in conversation["seller"]
    repeat = buyer.post(path, json={"message": "Duplicate initial message"})
    assert repeat.status_code == 200 and repeat.json()["id"] == conversation_id
    messages_path = f"/api/conversations/{conversation_id}/messages"
    assert buyer.get(messages_path).json()["total"] == 1
    assert stranger.get(messages_path).status_code in {403, 404}
    assert stranger.post(messages_path, json={"body": "Intrusion"}).status_code in {403, 404}
    assert stranger.get("/api/conversations").json()["total"] == 0
    response = seller.post(messages_path, json={"body": "Yes! Meet at the library at noon."})
    assert response.status_code == 201
    assert response.json()["sender_id"] == seller_user["id"]
    messages = buyer.get(messages_path).json()
    assert messages["total"] == 2
    assert [message["sender_id"] for message in messages["items"]] == [buyer_user["id"], seller_user["id"]]
    assert seller.get("/api/conversations").json()["items"][0]["last_message"] == "Yes! Meet at the library at noon."
    assert seller.patch(f"/api/listings/{listing['id']}", json={"status": "sold"}).status_code == 200
    assert stranger.post(path, json={"message": "Can I buy this?"}).status_code == 409
    assert seller.delete(f"/api/listings/{listing['id']}").status_code == 204
    assert market.client().get(f"/api/listings/{listing['id']}").status_code == 404
    assert seller.get("/api/me/listings").json()["total"] == 0
    assert buyer.get(messages_path).json()["total"] == 2
    assert buyer.get("/api/conversations").json()["items"][0]["listing"]["title"] == listing["title"]


def test_blank_messages_and_demo_contact_are_rejected(market):
    seller, _ = market.profile("seller@mit.edu")
    buyer, _ = market.profile("buyer@stanford.edu")
    listing = market.listing(seller)
    path = f"/api/listings/{listing['id']}/conversations"
    assert buyer.post(path, json={"message": "    "}).status_code == 422
    response = buyer.post(path, json={"message": "Hello, is this available?"})
    conversation_id = response.json()["id"]
    assert seller.post(f"/api/conversations/{conversation_id}/messages", json={"body": "   "}).status_code == 422
    other = market.listing(seller, title="Demo textbook")
    market.database.listings.update_one({"_id": other["id"]}, {"$set": {"is_demo": True}})
    response = buyer.post(f"/api/listings/{other['id']}/conversations", json={"message": "Interested!"})
    assert response.status_code == 409


def test_server_owned_fields_cannot_transfer_listing_ownership(market):
    seller, seller_user = market.profile("seller@mit.edu")
    buyer, buyer_user = market.profile("buyer@stanford.edu")
    listing = market.listing(seller)
    response = seller.patch(f"/api/listings/{listing['id']}", json={"seller_id": buyer_user["id"], "is_demo": True})
    assert response.status_code == 422
    persisted = buyer.get(f"/api/listings/{listing['id']}").json()
    assert persisted["seller"]["id"] == seller_user["id"] and persisted["is_demo"] is False


def test_simultaneous_buyer_requests_create_one_conversation(market):
    seller, _ = market.profile("seller@mit.edu")
    buyer, _ = market.profile("buyer@stanford.edu")
    listing = market.listing(seller)
    path = f"/api/listings/{listing['id']}/conversations"
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: buyer.post(path, json={"message": "Can I collect this today?"}), range(3)))
    assert all(response.status_code in {200, 201} for response in responses)
    assert sum(response.status_code == 201 for response in responses) == 1
    assert len({response.json()["id"] for response in responses}) == 1
    conversation_id = responses[0].json()["id"]
    assert buyer.get(f"/api/conversations/{conversation_id}/messages").json()["total"] == 1


def test_one_student_can_buy_and_sell_with_the_same_profile(market):
    first, first_user = market.profile("first@mit.edu", "First Student")
    second, second_user = market.profile("second@stanford.edu", "Second Student")
    textbook = market.listing(first)
    lamp = market.listing(second, title="Study desk lamp", category="electronics")
    buying = first.post(f"/api/listings/{lamp['id']}/conversations", json={"message": "I would like to buy your lamp. We can arrange payment at pickup."})
    selling = second.post(f"/api/listings/{textbook['id']}/conversations", json={"message": "Is your textbook available?"})
    assert buying.status_code == selling.status_code == 201
    assert buying.json()["buyer"]["id"] == first_user["id"]
    assert selling.json()["seller"]["id"] == first_user["id"]
    assert buying.json()["seller"]["id"] == second_user["id"]
    assert selling.json()["buyer"]["id"] == second_user["id"]
    assert first.get("/api/conversations").json()["total"] == 2
    assert first.get("/api/me/listings").json()["items"][0]["id"] == textbook["id"]
    assert first.patch(f"/api/listings/{textbook['id']}", json={"status": "sold"}).status_code == 200
    assert first.post(f"/api/conversations/{buying.json()['id']}/messages", json={"body": "I can meet at the library."}).status_code == 201
