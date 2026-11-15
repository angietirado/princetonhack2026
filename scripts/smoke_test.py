"""Exercise domain-only .edu profiles and marketplace flows on local Docker."""

from __future__ import annotations

import argparse
import uuid
from urllib.parse import urlparse

import httpx


def local_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise argparse.ArgumentTypeError("Use a loopback HTTP URL for this development smoke test.")
    return value.rstrip("/")


def expect(response: httpx.Response, status: int):
    if response.status_code != status:
        raise AssertionError(f"{response.request.method} {response.request.url.path}: expected {status}, got {response.status_code}: {response.text}")
    return response.json() if response.content else None


def check_student(client: httpx.Client, email: str, name: str) -> dict:
    profile = expect(client.post("/api/student/verify", json={"email": email, "display_name": name}), 200)
    assert profile["edu_eligible"] is True and profile["email_verified"] is False
    assert expect(client.get("/api/me"), 200)["id"] == profile["id"]
    return profile


def create_listing(client: httpx.Client, title: str) -> dict:
    return expect(client.post("/api/listings", json={
        "title": title,
        "description": "A temporary listing created by the local integration smoke test.",
        "price_cents": 2500, "category": "books", "condition": "good",
        "pickup_location": "Campus library", "image_url": None,
    }), 201)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", type=local_url, default="http://localhost:8080")
    args = parser.parse_args()
    suffix = uuid.uuid4().hex[:10]
    with httpx.Client(base_url=args.api, timeout=15) as first, httpx.Client(base_url=args.api, timeout=15) as second, httpx.Client(base_url=args.api, timeout=15) as other_browser:
        assert expect(first.get("/api/health"), 200)["database"] == "ok"
        config = expect(first.get("/api/config"), 200)
        assert config["verification_mode"] == "domain_only"
        expect(first.post("/api/student/verify", json={"email": "someone@example.com"}), 422)
        email = f"smoke-first-{suffix}@example.edu"
        first_profile = check_student(first, email, "First Student")
        second_profile = check_student(second, f"smoke-second-{suffix}@example.edu", "Second Student")
        duplicate_profile = check_student(other_browser, email, "Separate Browser")
        assert duplicate_profile["id"] != first_profile["id"]
        owned = []
        try:
            listing = create_listing(first, f"Smoke test textbook {suffix}")
            owned.append((first, listing["id"]))
            found = expect(second.get("/api/listings", params={"q": suffix}), 200)
            assert any(item["id"] == listing["id"] for item in found["items"])
            assert "email" not in listing["seller"]
            assert other_browser.patch(f"/api/listings/{listing['id']}", json={"price_cents": 1}).status_code in {403, 404}
            conversation = expect(second.post(f"/api/listings/{listing['id']}/conversations", json={"message": "Is pickup tomorrow possible?"}), 201)
            conversation_id = conversation["id"]
            assert conversation["buyer"]["id"] == second_profile["id"]
            assert other_browser.get(f"/api/conversations/{conversation_id}/messages").status_code in {403, 404}
            expect(first.post(f"/api/conversations/{conversation_id}/messages", json={"body": "Yes. We can arrange payment when we meet."}), 201)
            assert expect(second.get(f"/api/conversations/{conversation_id}/messages"), 200)["total"] == 2

            # The same two profiles can trade in the other direction without switching roles.
            second_listing = create_listing(second, f"Smoke test notes {suffix}")
            owned.append((second, second_listing["id"]))
            reverse = expect(first.post(f"/api/listings/{second_listing['id']}/conversations", json={"message": "I would like to buy your notes."}), 201)
            assert reverse["buyer"]["id"] == first_profile["id"]
            assert reverse["seller"]["id"] == second_profile["id"]
            expect(first.patch(f"/api/listings/{listing['id']}", json={"status": "reserved"}), 200)
            expect(first.patch(f"/api/listings/{listing['id']}", json={"status": "sold"}), 200)
            assert not any(item["id"] == listing["id"] for item in expect(second.get("/api/listings", params={"q": suffix}), 200)["items"])
        finally:
            for owner, listing_id in owned:
                expect(owner.delete(f"/api/listings/{listing_id}"), 204)
        for client in (first, second, other_browser):
            expect(client.delete("/api/student/session"), 204)
            expect(client.get("/api/me"), 401)
        if config["devmode"]:
            students = expect(first.get("/api/dev/students"), 200)["items"]
            assert {student["username"] for student in students} == {"dev_student1", "dev_student2"}
            one = expect(first.post("/api/dev/session", json={"username": "dev_student1"}), 200)
            two = expect(second.post("/api/dev/session", json={"username": "dev_student2"}), 200)
            assert one["id"] != two["id"]
            for client, student in ((first, one), (second, two)):
                owned_listings = expect(client.get("/api/me/listings"), 200)["items"]
                assert all(item["seller"]["id"] == student["id"] for item in owned_listings)
            assert expect(first.post("/api/dev/session", json={"username": "dev_student2"}), 200)["id"] == two["id"]
            assert expect(first.post("/api/dev/session", json={"username": "dev_student1"}), 200)["id"] == one["id"]
            assert expect(second.get("/api/me"), 200)["id"] == two["id"]
            expect(first.delete("/api/student/session"), 204)
            expect(second.delete("/api/student/session"), 204)
            print("PASS: reusable development students and independent browser switching")
    print("PASS: .edu check, real MongoDB, same-email browser isolation, both buying and selling, messages, listing controls, profile clearing")


if __name__ == "__main__":
    main()
