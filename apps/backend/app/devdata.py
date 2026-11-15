"""Reusable development identities, initialized only by an enabled app."""

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

DEV_USERNAMES = ("dev_student1", "dev_student2")


def dev_user_id(username: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"campus-exchange/dev/users/{username}"))


def ensure_dev_data(database) -> None:
    """Create missing fixtures, without resetting edits, sales, or soft deletions."""
    now = datetime.now(timezone.utc)
    samples = (
        ("Desk lamp", "Adjustable desk lamp, ready for a dorm room. A development listing for trying buyer and seller messages.", 1500, "electronics", "Student center"),
        ("Calculus textbook", "Calculus textbook in good condition. A development listing for trying listings and campus pickup.", 2500, "books", "Library entrance"),
    )
    for username, (title, description, price, category, pickup) in zip(DEV_USERNAMES, samples):
        user_id = dev_user_id(username)
        database.users.update_one(
            {"_id": user_id},
            {"$setOnInsert": {
                "email": f"{username}@example.edu", "display_name": username,
                "university": "example.edu", "edu_eligible": True,
                "email_verified": False, "dev_username": username, "created_at": now,
            }},
            upsert=True,
        )
        listing_id = str(uuid5(NAMESPACE_URL, f"campus-exchange/dev/listings/{username}"))
        database.listings.update_one(
            {"_id": listing_id},
            {"$setOnInsert": {
                "title": title, "description": description, "price_cents": price,
                "category": category, "condition": "good", "pickup_location": pickup,
                "image_url": None, "seller_id": user_id, "status": "available",
                "created_at": now, "updated_at": now, "deleted": False,
                "is_demo": False, "is_dev": True,
            }},
            upsert=True,
        )
