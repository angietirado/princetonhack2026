"""Pickup coordination and student repair services; no payment processing."""

import re
from typing import Literal
from uuid import uuid4

from fastapi import Depends, HTTPException, Query, Response
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .schemas import (Order, OrderInput, OrderPatch, Page, Repairer, RepairerInput,
                      RepairRequest, RepairRequestInput, RepairRequestPatch)


def install_marketplace_routes(application, *, db, current_user, visible_data, listing_json,
                               listing_or_404, public_user, utcnow, iso, page_result, rate_limit):
    def conversation_for(database, listing, buyer, message, *, record_message=True):
        query = {"listing_id": listing["_id"], "buyer_id": buyer["_id"]}
        existing = database.conversations.find_one(query)
        if existing:
            return existing["_id"]
        now = utcnow()
        document = {"_id": str(uuid4()), **query, "seller_id": listing["seller_id"],
                    "last_message": message, "created_at": now, "updated_at": now,
                    "is_dev": bool(buyer.get("dev_username") or listing.get("is_dev"))}
        try:
            database.conversations.insert_one(document)
        except DuplicateKeyError:
            return database.conversations.find_one(query)["_id"]
        if record_message:
            database.messages.insert_one({"_id": str(uuid4()), "conversation_id": document["_id"],
                                          "sender_id": buyer["_id"], "body": message, "created_at": now})
        return document["_id"]

    def order_json(document, database):
        current = database.listings.find_one({"_id": document["listing_id"]})
        snapshot = {**document.get("listing_snapshot", current), "status": current["status"]}
        return {"id": document["_id"], "listing": listing_json(snapshot, database),
                "buyer": public_user(database.users.find_one({"_id": document["buyer_id"]})),
                "seller": public_user(database.users.find_one({"_id": document["seller_id"]})),
                **{key: document[key] for key in ("status", "pickup_location", "conversation_id")},
                "created_at": iso(document["created_at"]), "updated_at": iso(document["updated_at"])}

    def own_order(database, order_id, user):
        document = database.orders.find_one(visible_data({"_id": order_id, "$or": [{"buyer_id": user["_id"]}, {"seller_id": user["_id"]}]}))
        if not document:
            raise HTTPException(404, "Pickup request not found")
        return document

    @application.post("/api/listings/{listing_id}/orders", response_model=Order, status_code=201, tags=["orders"])
    def create_order(listing_id: str, payload: OrderInput, response: Response, user=Depends(current_user), database=Depends(db)):
        listing = listing_or_404(database, listing_id)
        if listing["seller_id"] == user["_id"]:
            raise HTTPException(400, "You cannot request your own listing")
        if listing.get("is_demo"):
            raise HTTPException(409, "This is a display-only sample listing")
        request_key = f"{listing_id}:{user['_id']}"
        existing = database.orders.find_one(visible_data({"request_key": request_key}))
        if existing:
            response.status_code = 200
            return order_json(existing, database)
        if listing["status"] != "available":
            raise HTTPException(409, "This item is no longer available")
        rate_limit(database, f"order:{user['_id']}", 30)
        now = utcnow()
        document = {"_id": str(uuid4()), "listing_id": listing_id, "buyer_id": user["_id"], "seller_id": listing["seller_id"],
                    "request_key": request_key,
                    "listing_snapshot": listing,
                    "kind": listing.get("kind", "sale"), "status": "requested", "pickup_location": payload.pickup_location or listing["pickup_location"],
                    "conversation_id": conversation_for(database, listing, user, payload.message, record_message=False), "created_at": now, "updated_at": now,
                    "is_dev": bool(user.get("dev_username") or listing.get("is_dev"))}
        try:
            database.orders.insert_one(document)
        except DuplicateKeyError:
            document = database.orders.find_one({"request_key": request_key})
            response.status_code = 200
        else:
            # Only the winning insert writes the request message. Repeated POSTs
            # reuse the order, while a new request in an existing chat is delivered.
            database.messages.insert_one({"_id": str(uuid4()), "conversation_id": document["conversation_id"],
                                          "sender_id": user["_id"], "body": payload.message, "created_at": now})
            database.conversations.update_one({"_id": document["conversation_id"]}, {"$set": {"last_message": payload.message, "updated_at": now}})
        return order_json(document, database)

    @application.get("/api/orders", response_model=Page[Order], tags=["orders"])
    def orders(role: Literal["buying", "selling", "borrowing"] | None = None,
               page: int = Query(default=1, ge=1), page_size: int = Query(default=24, ge=1, le=100),
               user=Depends(current_user), database=Depends(db)):
        if role == "selling":
            query = {"seller_id": user["_id"]}
        elif role == "borrowing":
            query = {"buyer_id": user["_id"], "kind": "rent"}
        elif role == "buying":
            query = {"buyer_id": user["_id"], "kind": {"$ne": "rent"}}
        else:
            query = {"$or": [{"buyer_id": user["_id"]}, {"seller_id": user["_id"]}]}
        query = visible_data(query)
        rows = database.orders.find(query).sort([("updated_at", -1), ("_id", 1)]).skip((page - 1) * page_size).limit(page_size)
        return page_result([order_json(item, database) for item in rows], database.orders.count_documents(query), page, page_size)

    @application.get("/api/orders/{order_id}", response_model=Order, tags=["orders"])
    def order_detail(order_id: str, user=Depends(current_user), database=Depends(db)):
        return order_json(own_order(database, order_id, user), database)

    @application.patch("/api/orders/{order_id}", response_model=Order, tags=["orders"])
    def update_order(order_id: str, payload: OrderPatch, user=Depends(current_user), database=Depends(db)):
        order = own_order(database, order_id, user)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(422, "Provide a status or pickup location")
        seller = user["_id"] == order["seller_id"]
        before = order["status"]
        after = changes.get("status", before)
        terminal = {"cancelled", "returned"} | ({"completed"} if order["kind"] != "rent" else set())
        if "pickup_location" in changes and (not seller or before in terminal or before == "completed"):
            raise HTTPException(403, "Only the seller can update pickup before completion")
        if after != before:
            allowed = (seller and (before, after) in {("requested", "accepted"), ("accepted", "ready")})
            allowed = allowed or (not seller and (before, after) == ("ready", "completed"))
            allowed = allowed or (seller and order["kind"] == "rent" and (before, after) == ("completed", "returned"))
            allowed = allowed or (after == "cancelled" and before in {"requested", "accepted", "ready"})
            if not allowed:
                raise HTTPException(409, "This pickup status change is not allowed for your role")
        now = utcnow()
        claimed = False
        if before == "requested" and after == "accepted":
            terms = order["listing_snapshot"]
            claim = {"_id": order["listing_id"], "deleted": {"$ne": True}, "status": "available", "active_order_id": {"$exists": False},
                     "price_cents": terms["price_cents"], "kind": {"$in": ["sale", None]} if order["kind"] == "sale" else order["kind"]}
            result = database.listings.update_one(claim,
                                                 {"$set": {"active_order_id": order_id, "status": "reserved", "updated_at": now}})
            if not result.modified_count:
                raise HTTPException(409, "The item is unavailable or its price/type changed. Cancel and request it again.")
            claimed = True
        changes["updated_at"] = now
        update = {"$set": changes}
        if after in {"cancelled", "returned"}:
            update["$unset"] = {"request_key": ""}
        updated = database.orders.find_one_and_update({"_id": order_id, "status": before, "updated_at": order["updated_at"]}, update, return_document=ReturnDocument.AFTER)
        if not updated:
            if claimed:
                database.listings.update_one({"_id": order["listing_id"], "active_order_id": order_id}, {"$set": {"status": "available", "updated_at": now}, "$unset": {"active_order_id": ""}})
            raise HTTPException(409, "The pickup changed. Refresh and try again.")
        if after != before and after in {"cancelled", "returned"}:
            database.listings.update_one({"_id": order["listing_id"], "active_order_id": order_id}, {"$set": {"status": "available", "updated_at": now}, "$unset": {"active_order_id": ""}})
        elif after != before and after == "completed" and order["kind"] != "rent":
            database.listings.update_one({"_id": order["listing_id"], "active_order_id": order_id}, {"$set": {"status": "sold", "updated_at": now}})
            database.orders.update_many({"listing_id": order["listing_id"], "_id": {"$ne": order_id}, "status": "requested"}, {"$set": {"status": "cancelled", "updated_at": now}, "$unset": {"request_key": ""}})
        return order_json(updated, database)

    def repairer_json(document, database):
        return {"id": document["_id"], "user": public_user(database.users.find_one({"_id": document["user_id"]})),
                **{key: document[key] for key in ("specialties", "about", "rate_cents", "portfolio_image_url")},
                "verification": "self_reported", "created_at": iso(document["created_at"]), "updated_at": iso(document["updated_at"])}

    @application.get("/api/repairers", response_model=Page[Repairer], tags=["repairs"])
    def repairers(specialty: str | None = Query(default=None, max_length=60), page: int = Query(default=1, ge=1),
                  page_size: int = Query(default=24, ge=1, le=100), database=Depends(db)):
        query = visible_data({})
        if specialty and specialty.strip():
            query["specialties"] = {"$regex": re.escape(specialty.strip()), "$options": "i"}
        rows = database.repairers.find(query).sort([("updated_at", -1), ("_id", 1)]).skip((page - 1) * page_size).limit(page_size)
        return page_result([repairer_json(item, database) for item in rows], database.repairers.count_documents(query), page, page_size)

    @application.get("/api/repairers/me", response_model=Repairer, tags=["repairs"])
    def own_repairer(user=Depends(current_user), database=Depends(db)):
        item = database.repairers.find_one(visible_data({"user_id": user["_id"]}))
        if not item:
            raise HTTPException(404, "No repair profile yet")
        return repairer_json(item, database)

    @application.post("/api/repairers/me", response_model=Repairer, tags=["repairs"])
    def apply_repairer(payload: RepairerInput, user=Depends(current_user), database=Depends(db)):
        rate_limit(database, f"repair-profile:{user['_id']}", 30)
        now = utcnow()
        item = database.repairers.find_one_and_update({"user_id": user["_id"]},
            {"$set": {**payload.model_dump(), "updated_at": now}, "$setOnInsert": {"_id": str(uuid4()), "created_at": now, "is_dev": bool(user.get("dev_username"))}},
            upsert=True, return_document=ReturnDocument.AFTER)
        return repairer_json(item, database)

    def request_json(document, database):
        return {"id": document["_id"], "repairer": repairer_json(database.repairers.find_one({"_id": document["repairer_id"]}), database),
                "requester": public_user(database.users.find_one({"_id": document["requester_id"]})),
                **{key: document[key] for key in ("description", "location", "status", "conversation_id")},
                "created_at": iso(document["created_at"]), "updated_at": iso(document["updated_at"])}

    def own_request(database, request_id, user):
        item = database.repair_requests.find_one(visible_data({"_id": request_id, "$or": [{"requester_id": user["_id"]}, {"repairer_user_id": user["_id"]}]}))
        if not item:
            raise HTTPException(404, "Repair request not found")
        return item

    @application.post("/api/repairers/{repairer_id}/requests", response_model=RepairRequest, status_code=201, tags=["repairs"])
    def request_repair(repairer_id: str, payload: RepairRequestInput, user=Depends(current_user), database=Depends(db)):
        repairer = database.repairers.find_one(visible_data({"_id": repairer_id}))
        if not repairer:
            raise HTTPException(404, "Repairer not found")
        if repairer["user_id"] == user["_id"]:
            raise HTTPException(400, "You cannot request your own repair service")
        rate_limit(database, f"repair-request:{user['_id']}", 30)
        now = utcnow()
        dev = bool(user.get("dev_username") or repairer.get("is_dev"))
        # A private service record gives repair bookings the same participant-only
        # messaging path as marketplace pickups. It never appears in public lists.
        listing = {"_id": str(uuid4()), "title": f"Repair: {payload.description[:80]}", "description": payload.description,
                   "price_cents": repairer["rate_cents"], "category": "other", "condition": "fair", "kind": "sale", "needs_repair": True,
                   "pickup_location": payload.location, "image_url": None, "seller_id": repairer["user_id"], "status": "available",
                   "created_at": now, "updated_at": now, "deleted": False, "is_demo": False, "is_dev": dev, "is_service": True}
        database.listings.insert_one(listing)
        item = {"_id": str(uuid4()), "repairer_id": repairer_id, "repairer_user_id": repairer["user_id"], "requester_id": user["_id"],
                **payload.model_dump(), "status": "requested", "conversation_id": conversation_for(database, listing, user, payload.description),
                "created_at": now, "updated_at": now, "is_dev": dev}
        database.repair_requests.insert_one(item)
        return request_json(item, database)

    @application.get("/api/repair-requests", response_model=Page[RepairRequest], tags=["repairs"])
    def repair_requests(page: int = Query(default=1, ge=1), page_size: int = Query(default=24, ge=1, le=100),
                        user=Depends(current_user), database=Depends(db)):
        query = visible_data({"$or": [{"requester_id": user["_id"]}, {"repairer_user_id": user["_id"]}]})
        rows = database.repair_requests.find(query).sort([("updated_at", -1), ("_id", 1)]).skip((page - 1) * page_size).limit(page_size)
        return page_result([request_json(item, database) for item in rows], database.repair_requests.count_documents(query), page, page_size)

    @application.get("/api/repair-requests/{request_id}", response_model=RepairRequest, tags=["repairs"])
    def repair_request_detail(request_id: str, user=Depends(current_user), database=Depends(db)):
        return request_json(own_request(database, request_id, user), database)

    @application.patch("/api/repair-requests/{request_id}", response_model=RepairRequest, tags=["repairs"])
    def update_repair_request(request_id: str, payload: RepairRequestPatch, user=Depends(current_user), database=Depends(db)):
        item = own_request(database, request_id, user)
        before, after = item["status"], payload.status
        provider = user["_id"] == item["repairer_user_id"]
        allowed = before == after or (provider and (before, after) in {("requested", "accepted"), ("accepted", "completed")})
        allowed = allowed or (after == "cancelled" and before in {"requested", "accepted"})
        if not allowed:
            raise HTTPException(409, "This repair status change is not allowed for your role")
        updated = database.repair_requests.find_one_and_update({"_id": request_id, "status": before, "updated_at": item["updated_at"]},
                                                              {"$set": {"status": after, "updated_at": utcnow()}}, return_document=ReturnDocument.AFTER)
        if not updated:
            raise HTTPException(409, "The repair request changed. Refresh and try again.")
        return request_json(updated, database)
