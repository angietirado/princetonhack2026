from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure


def ensure_indexes(database) -> None:
    # An entered address is an eligibility hint, never an identity credential.
    # Keep existing profiles and listings, but let separate browsers use the same
    # address without sharing access to each other's private data.
    for name, index in database.users.index_information().items():
        if index.get("unique") and list(index.get("key", [])) == [("email", ASCENDING)]:
            try:
                database.users.drop_index(name)
            except OperationFailure as exc:
                if exc.code != 27:  # Another startup process may have dropped it.
                    raise
    database.users.create_index("email")
    database.sessions.create_index("expires_at", expireAfterSeconds=0)
    database.rate_limits.create_index("expires_at", expireAfterSeconds=0)
    database.listings.create_index([("deleted", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)])
    database.listings.create_index([("seller_id", ASCENDING), ("created_at", DESCENDING)])
    database.conversations.create_index([("listing_id", ASCENDING), ("buyer_id", ASCENDING)], unique=True)
    database.conversations.create_index([("buyer_id", ASCENDING), ("updated_at", DESCENDING)])
    database.conversations.create_index([("seller_id", ASCENDING), ("updated_at", DESCENDING)])
    database.messages.create_index([("conversation_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)])
    database.orders.create_index("request_key", unique=True, sparse=True)
    database.orders.create_index([("seller_id", ASCENDING), ("updated_at", DESCENDING)])
    database.orders.create_index([("buyer_id", ASCENDING), ("updated_at", DESCENDING)])
    database.repairers.create_index("user_id", unique=True)
    database.repair_requests.create_index([("requester_id", ASCENDING), ("updated_at", DESCENDING)])
    database.repair_requests.create_index([("repairer_user_id", ASCENDING), ("updated_at", DESCENDING)])
