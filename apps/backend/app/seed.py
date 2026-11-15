"""Explicit, idempotent demo fixtures: python -m app.seed. Never runs at startup."""

from datetime import timedelta

from pymongo import MongoClient

from .config import Settings
from .database import ensure_indexes
from .main import utcnow

SAMPLES = [
    ("books", "The books for your next chapter", "A well-kept set of introductory course books. Sample listing for exploring the marketplace.", 2500, "good", "Library entrance"),
    ("electronics", "A little music between classes", "Compact wireless headphones with a protective case. Sample listing for exploring the marketplace.", 4500, "like_new", "Student center"),
    ("furniture", "Your new favorite study spot", "Small wooden desk for a dorm room, with plenty of room for notes and coffee. Sample listing.", 6000, "good", "North campus"),
    ("clothing", "An extra layer for late nights", "Comfortable university-style crewneck sweatshirt. Sample listing for exploring the marketplace.", 1800, "like_new", "Campus green"),
    ("sports", "Take the scenic route", "A sturdy campus bicycle for trips between classes. Sample listing for exploring the marketplace.", 9500, "good", "Bike racks"),
    ("other", "Make a little room for green", "A small low-maintenance plant in a ceramic pot. Sample listing for exploring the marketplace.", 1200, "new", "Student center"),
]


def seed(database) -> int:
    ensure_indexes(database)
    now = utcnow()
    database.users.update_one({"_id": "demo-student"}, {"$setOnInsert": {"email": "sample@demo.edu", "display_name": "Sample Student", "university": "Demo campus", "created_at": now}, "$set": {"edu_eligible": True, "email_verified": False}, "$unset": {"verified": ""}}, upsert=True)
    for index, (category, title, description, price, condition, pickup) in enumerate(SAMPLES):
        database.listings.update_one({"_id": f"demo-listing-{index + 1}"}, {"$setOnInsert": {"title": title, "description": description, "price_cents": price, "category": category, "condition": condition, "pickup_location": pickup, "image_url": None, "seller_id": "demo-student", "status": "available", "created_at": now - timedelta(hours=index), "updated_at": now, "deleted": False, "is_demo": True}}, upsert=True)
    return len(SAMPLES)


if __name__ == "__main__":
    config = Settings()
    with MongoClient(config.mongodb_uri, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        print(f"Created or retained {seed(client[config.mongodb_database])} clearly labeled sample listings.")
