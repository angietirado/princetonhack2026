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
    # Extra fixture records support the partner's sale/rental/free/request cards.
    # Stable IDs and insert-only writes preserve edits and soft deletions.
    partner_samples = (
        ("mini-fridge", "dev_student1", "Mini fridge", "Door seal is loose. A useful dorm fridge offered as-is for a student who can fix the seal.", 4000, "electronics", "Redwood Hall", "sale", True),
        ("calculator", "dev_student2", "TI-84 calculator", "Works perfectly and is ready for classes. Arrange pickup at the GITC lobby.", 2500, "electronics", "GITC", "sale", False),
        ("bike", "dev_student1", "Commuter bike", "Step-through bicycle available to borrow by the day. Agree on the return time in messages.", 500, "sports", "Maple Hall", "rent", False),
        ("packing-tape", "dev_student2", "Packing tape", "A free opened roll of packing tape with plenty left for your next project.", 0, "other", "Campus Center", "free", False),
        ("urgent-tape", "dev_student1", "Need packing tape for a poster", "Looking for a little packing tape for a poster today. Please message if you can help.", 0, "other", "Campus Center", "urgent", False),
        ("wanted-lamp", "dev_student2", "Looking for a reading lamp", "Looking for a small reading lamp for my desk. Let me know if you have one to offer.", 0, "electronics", "Maple Hall", "wanted", False),
    )
    for key, username, title, description, price, category, location, kind, needs_repair in partner_samples:
        listing_id = str(uuid5(NAMESPACE_URL, f"campus-exchange/dev/partner-listings/{key}"))
        database.listings.update_one({"_id": listing_id}, {"$setOnInsert": {
            "title": title, "description": description, "price_cents": price, "category": category,
            "condition": "fair" if needs_repair else "good", "pickup_location": location, "kind": kind,
            "needs_repair": needs_repair, "image_url": None, "seller_id": dev_user_id(username),
            "status": "available", "created_at": now, "updated_at": now, "deleted": False,
            "is_demo": False, "is_dev": True, "earned_iou": kind == "free",
        }}, upsert=True)
    for username, specialties, about, rate in (
        ("dev_student1", ["Appliances", "Fans", "Lamps"], "I can help diagnose small appliances, lamps, and fans. Please describe the issue before arranging a meetup.", 1500),
        ("dev_student2", ["Laptops", "Bikes"], "I enjoy fixing laptops and bikes. These are self-reported skills; we can discuss the job and price in messages.", 2000),
    ):
        repairer_id = str(uuid5(NAMESPACE_URL, f"campus-exchange/dev/repairers/{username}"))
        database.repairers.update_one({"_id": repairer_id}, {"$setOnInsert": {
            "user_id": dev_user_id(username), "specialties": specialties, "about": about, "rate_cents": rate,
            "portfolio_image_url": None, "created_at": now, "updated_at": now, "is_dev": True,
        }}, upsert=True)
