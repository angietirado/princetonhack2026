"""FastAPI routes. Synchronous PyMongo I/O runs in FastAPI's thread pool."""

import hashlib
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from .config import Settings
from .database import ensure_indexes
from .devdata import DEV_USERNAMES, dev_user_id, ensure_dev_data
from .schemas import (
    AppConfig, Category, Conversation, ConversationInput, DevSessionInput, DevStudents,
    Health, Listing, ListingInput, ListingPatch, Message, MessageInput, Page,
    ProfileInput, StudentInput, User,
)

COOKIE_NAME = "campus_session"
logger = logging.getLogger("campus.api")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def public_user(user: dict) -> dict:
    return {"id": user["_id"], "display_name": user["display_name"], "university": user["university"], "edu_eligible": True, "email_verified": False}


def private_user(user: dict) -> dict:
    return {**public_user(user), "email": user["email"], "created_at": iso(user["created_at"]), "dev_username": user.get("dev_username")}


def page_result(items: list, total: int, page: int, page_size: int) -> dict:
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def create_app(settings: Settings | None = None, database=None) -> FastAPI:
    config = settings or Settings()
    injected_database = database

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        client = None
        if injected_database is None:
            client = MongoClient(config.mongodb_uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, tz_aware=True)
            application.state.db = client[config.mongodb_database]
        else:
            application.state.db = injected_database
        # Fail startup clearly rather than accepting writes without indexes.
        ensure_indexes(application.state.db)
        if config.devmode:
            ensure_dev_data(application.state.db)
        try:
            yield
        finally:
            if client is not None:
                client.close()

    application = FastAPI(title="Campus Exchange API", version="0.1.0", lifespan=lifespan)
    application.state.settings = config
    if injected_database is not None:
        application.state.db = injected_database
    application.add_middleware(
        CORSMiddleware, allow_origins=list(config.allowed_origins), allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def protect_browser_mutations(request: Request, call_next):
        if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
            origin = request.headers.get("origin")
            if origin is not None and origin.rstrip("/") not in config.allowed_origins:
                return JSONResponse(status_code=403, content={"detail": "This browser origin is not allowed"})
            if request.headers.get("sec-fetch-site") == "cross-site" and origin is None:
                return JSONResponse(status_code=403, content={"detail": "Cross-site requests are not allowed"})
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            result.headers["Cache-Control"] = "no-store"
        return result

    @application.exception_handler(PyMongoError)
    async def database_failure(request: Request, exc: PyMongoError):
        logger.error("Database operation failed: %s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Database is temporarily unavailable. Please try again."})

    def db(request: Request):
        return request.app.state.db

    def browser_profile(request: Request, database) -> dict | None:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        session = database.sessions.find_one({"_id": token_hash(token), "expires_at": {"$gt": utcnow()}})
        user = database.users.find_one({"_id": session["user_id"]}) if session else None
        if not user or not user.get("email", "").rsplit("@", 1)[-1].endswith(".edu"):
            return None
        if user.get("dev_username") and not config.devmode:
            return None
        return user

    def visible_data(query: dict) -> dict:
        if not config.devmode:
            query["is_dev"] = {"$ne": True}
        return query

    def remember_profile(user: dict, request: Request, response: Response, database) -> None:
        previous = request.cookies.get(COOKIE_NAME)
        if previous:
            database.sessions.delete_one({"_id": token_hash(previous)})
        token = secrets.token_urlsafe(32)
        now = utcnow()
        expires_at = now + timedelta(days=config.session_days)
        database.sessions.insert_one({"_id": token_hash(token), "user_id": user["_id"], "expires_at": expires_at, "created_at": now})
        response.set_cookie(COOKIE_NAME, token, max_age=config.session_days * 86400, httponly=True, secure=config.cookie_secure, samesite="lax", path="/")

    def current_user(request: Request, database=Depends(db)) -> dict:
        user = browser_profile(request, database)
        if not user:
            raise HTTPException(401, "Add your .edu email to start buying and selling in this browser.")
        return user

    def rate_limit(database, key: str, limit: int, seconds: int = 3600) -> None:
        now = utcnow()
        bucket = int(now.timestamp()) // seconds
        result = database.rate_limits.find_one_and_update(
            {"_id": f"{key}:{bucket}"},
            {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": datetime.fromtimestamp((bucket + 1) * seconds, timezone.utc)}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        if result["count"] > limit:
            retry = seconds - int(now.timestamp()) % seconds
            raise HTTPException(429, "Too many requests. Please try again later.", headers={"Retry-After": str(retry)})

    def listing_json(document: dict, database) -> dict:
        seller = database.users.find_one({"_id": document["seller_id"]})
        return {
            "id": document["_id"],
            **{key: document[key] for key in ("title", "description", "price_cents", "category", "condition", "pickup_location", "image_url", "status")},
            "seller": public_user(seller), "created_at": iso(document["created_at"]),
            "updated_at": iso(document["updated_at"]), "is_demo": document.get("is_demo", False),
        }

    def listing_or_404(database, listing_id: str) -> dict:
        listing = database.listings.find_one(visible_data({"_id": listing_id, "deleted": {"$ne": True}}))
        if not listing:
            raise HTTPException(404, "Listing not found")
        return listing

    def conversation_json(document: dict, database) -> dict:
        listing = database.listings.find_one({"_id": document["listing_id"]})
        return {
            "id": document["_id"],
            "listing": {"id": listing["_id"], "title": listing["title"], "price_cents": listing["price_cents"], "image_url": listing["image_url"], "status": "removed" if listing.get("deleted") else listing["status"]},
            "buyer": public_user(database.users.find_one({"_id": document["buyer_id"]})),
            "seller": public_user(database.users.find_one({"_id": document["seller_id"]})),
            "last_message": document["last_message"], "created_at": iso(document["created_at"]), "updated_at": iso(document["updated_at"]),
        }

    def participant_conversation(database, conversation_id: str, user_id: str) -> dict:
        conversation = database.conversations.find_one(visible_data({"_id": conversation_id, "$or": [{"buyer_id": user_id}, {"seller_id": user_id}]}))
        if not conversation:
            raise HTTPException(404, "Conversation not found")
        return conversation

    def message_json(document: dict) -> dict:
        return {"id": document["_id"], "conversation_id": document["conversation_id"], "sender_id": document["sender_id"], "body": document["body"], "created_at": iso(document["created_at"])}

    @application.get("/api/health", response_model=Health, tags=["system"])
    def health(database=Depends(db)):
        database.command("ping")
        return {"status": "ok", "database": "ok"}

    @application.get("/api/config", response_model=AppConfig, tags=["system"])
    def app_config(database=Depends(db)):
        return {"email_policy": "edu", "verification_mode": "domain_only", "demo_data": database.listings.count_documents({"is_demo": True, "deleted": {"$ne": True}}) > 0, "devmode": config.devmode}

    if config.devmode:
        @application.get("/api/dev/students", response_model=DevStudents, tags=["development"])
        def dev_students(database=Depends(db)):
            students = []
            for username in DEV_USERNAMES:
                user = database.users.find_one({"_id": dev_user_id(username)})
                students.append({"username": username, "id": user["_id"], "display_name": user["display_name"], "email": user["email"]})
            return {"items": students}

        @application.post("/api/dev/session", response_model=User, tags=["development"])
        def use_dev_student(payload: DevSessionInput, request: Request, response: Response, database=Depends(db)):
            user = database.users.find_one({"_id": dev_user_id(payload.username), "dev_username": payload.username})
            if not user:
                raise HTTPException(404, "Development student not found")
            remember_profile(user, request, response, database)
            return private_user(user)

    @application.post("/api/student/verify", response_model=User, tags=["student"])
    def check_student_email(payload: StudentInput, request: Request, response: Response, database=Depends(db)):
        # This validates only syntax and the .edu suffix. The cookie owns the
        # browser profile; knowing someone's email must never recover it.
        ip = request.client.host if request.client else "unknown"
        rate_limit(database, f"student-check:{ip}", config.student_check_limit_per_hour)
        now = utcnow()
        user = browser_profile(request, database)
        # A normal .edu check must not rewrite a shared development identity.
        if user and user.get("dev_username"):
            user = None
        default_name = re.sub(r"[._+-]+", " ", payload.email.split("@", 1)[0]).title()[:60]
        default_name = default_name if len(default_name) >= 2 else "Student"
        profile_fields = {
            "email": payload.email,
            "display_name": payload.display_name or (user["display_name"] if user else default_name),
            "university": payload.email.rsplit("@", 1)[1],
            "edu_eligible": True,
            "email_verified": False,
        }
        if user:
            user = database.users.find_one_and_update(
                {"_id": user["_id"]},
                {"$set": profile_fields, "$unset": {"verified": ""}},
                return_document=ReturnDocument.AFTER,
            )
        else:
            user = {"_id": str(uuid4()), **profile_fields, "created_at": now}
            database.users.insert_one(user)
        remember_profile(user, request, response, database)
        return private_user(user)

    @application.delete("/api/student/session", status_code=204, tags=["student"])
    def clear_browser_profile(request: Request, response: Response, database=Depends(db)):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            database.sessions.delete_one({"_id": token_hash(token)})
        response.delete_cookie(COOKIE_NAME, httponly=True, secure=config.cookie_secure, samesite="lax", path="/")

    @application.get("/api/me", response_model=User, tags=["account"])
    def me(user=Depends(current_user)):
        return private_user(user)

    @application.patch("/api/me", response_model=User, tags=["account"])
    def update_me(payload: ProfileInput, user=Depends(current_user), database=Depends(db)):
        updated = database.users.find_one_and_update({"_id": user["_id"]}, {"$set": {"display_name": payload.display_name}}, return_document=ReturnDocument.AFTER)
        return private_user(updated)

    @application.get("/api/listings", response_model=Page[Listing], tags=["listings"])
    def listings(
        q: str = Query(default="", max_length=100), category: Category | None = None,
        min_price: int | None = Query(default=None, ge=0), max_price: int | None = Query(default=None, ge=0),
        sort: Literal["newest", "price_asc", "price_desc"] = "newest",
        page: int = Query(default=1, ge=1), page_size: int = Query(default=24, ge=1, le=100), database=Depends(db),
    ):
        if min_price is not None and max_price is not None and min_price > max_price:
            raise HTTPException(422, "Minimum price cannot exceed maximum price")
        query: dict = visible_data({"deleted": {"$ne": True}, "status": {"$in": ["available", "reserved"]}})
        if q.strip():
            query["$or"] = [{field: {"$regex": re.escape(q.strip()), "$options": "i"}} for field in ("title", "description")]
        if category:
            query["category"] = category
        if min_price is not None or max_price is not None:
            query["price_cents"] = {}
            if min_price is not None:
                query["price_cents"]["$gte"] = min_price
            if max_price is not None:
                query["price_cents"]["$lte"] = max_price
        order = [("created_at", -1), ("_id", 1)] if sort == "newest" else [("price_cents", 1 if sort == "price_asc" else -1), ("_id", 1)]
        documents = database.listings.find(query).sort(order).skip((page - 1) * page_size).limit(page_size)
        return page_result([listing_json(item, database) for item in documents], database.listings.count_documents(query), page, page_size)

    @application.get("/api/me/listings", response_model=Page[Listing], tags=["account"])
    def my_listings(page: int = Query(default=1, ge=1), page_size: int = Query(default=24, ge=1, le=100), user=Depends(current_user), database=Depends(db)):
        query = visible_data({"seller_id": user["_id"], "deleted": {"$ne": True}})
        documents = database.listings.find(query).sort([("created_at", -1), ("_id", 1)]).skip((page - 1) * page_size).limit(page_size)
        return page_result([listing_json(item, database) for item in documents], database.listings.count_documents(query), page, page_size)

    @application.post("/api/listings", response_model=Listing, status_code=201, tags=["listings"])
    def create_listing(payload: ListingInput, user=Depends(current_user), database=Depends(db)):
        rate_limit(database, f"listing:{user['_id']}", 30)
        now = utcnow()
        document = {"_id": str(uuid4()), **payload.model_dump(), "seller_id": user["_id"], "status": "available", "created_at": now, "updated_at": now, "deleted": False, "is_demo": False, "is_dev": bool(user.get("dev_username"))}
        database.listings.insert_one(document)
        return listing_json(document, database)

    @application.get("/api/listings/{listing_id}", response_model=Listing, tags=["listings"])
    def listing_detail(listing_id: str, database=Depends(db)):
        return listing_json(listing_or_404(database, listing_id), database)

    @application.patch("/api/listings/{listing_id}", response_model=Listing, tags=["listings"])
    def update_listing(listing_id: str, payload: ListingPatch, user=Depends(current_user), database=Depends(db)):
        listing = listing_or_404(database, listing_id)
        if listing["seller_id"] != user["_id"]:
            raise HTTPException(403, "Only the seller can change this listing")
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(422, "Provide at least one field to update")
        changes["updated_at"] = utcnow()
        document = database.listings.find_one_and_update({"_id": listing_id, "seller_id": user["_id"], "deleted": {"$ne": True}}, {"$set": changes}, return_document=ReturnDocument.AFTER)
        if not document:
            raise HTTPException(404, "Listing not found")
        return listing_json(document, database)

    @application.delete("/api/listings/{listing_id}", status_code=204, tags=["listings"])
    def delete_listing(listing_id: str, user=Depends(current_user), database=Depends(db)):
        listing = listing_or_404(database, listing_id)
        if listing["seller_id"] != user["_id"]:
            raise HTTPException(403, "Only the seller can remove this listing")
        database.listings.update_one({"_id": listing_id, "seller_id": user["_id"], "deleted": {"$ne": True}}, {"$set": {"deleted": True, "updated_at": utcnow()}})

    @application.post("/api/listings/{listing_id}/conversations", response_model=Conversation, status_code=201, tags=["messages"])
    def start_conversation(listing_id: str, payload: ConversationInput, response: Response, user=Depends(current_user), database=Depends(db)):
        listing = listing_or_404(database, listing_id)
        if listing["seller_id"] == user["_id"]:
            raise HTTPException(400, "You cannot contact yourself about your own listing")
        if listing.get("is_demo"):
            raise HTTPException(409, "This is a sample listing. Create a real listing to try messaging.")
        existing = database.conversations.find_one({"listing_id": listing_id, "buyer_id": user["_id"]})
        if existing:
            response.status_code = 200
            return conversation_json(existing, database)
        if listing["status"] != "available":
            raise HTTPException(409, "This item is no longer available")
        rate_limit(database, f"conversation:{user['_id']}", 30)
        now = utcnow()
        document = {"_id": str(uuid4()), "listing_id": listing_id, "buyer_id": user["_id"], "seller_id": listing["seller_id"], "last_message": payload.message, "created_at": now, "updated_at": now, "is_dev": bool(user.get("dev_username") or listing.get("is_dev"))}
        try:
            database.conversations.insert_one(document)
        except DuplicateKeyError:
            response.status_code = 200
            return conversation_json(database.conversations.find_one({"listing_id": listing_id, "buyer_id": user["_id"]}), database)
        database.messages.insert_one({"_id": str(uuid4()), "conversation_id": document["_id"], "sender_id": user["_id"], "body": payload.message, "created_at": now})
        return conversation_json(document, database)

    @application.get("/api/conversations", response_model=Page[Conversation], tags=["messages"])
    def conversations(page: int = Query(default=1, ge=1), page_size: int = Query(default=24, ge=1, le=100), user=Depends(current_user), database=Depends(db)):
        query = visible_data({"$or": [{"buyer_id": user["_id"]}, {"seller_id": user["_id"]}]})
        documents = database.conversations.find(query).sort([("updated_at", -1), ("_id", 1)]).skip((page - 1) * page_size).limit(page_size)
        return page_result([conversation_json(item, database) for item in documents], database.conversations.count_documents(query), page, page_size)

    @application.get("/api/conversations/{conversation_id}/messages", response_model=Page[Message], tags=["messages"])
    def messages(conversation_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=100, ge=1, le=100), user=Depends(current_user), database=Depends(db)):
        participant_conversation(database, conversation_id, user["_id"])
        query = {"conversation_id": conversation_id}
        documents = database.messages.find(query).sort([("created_at", 1), ("_id", 1)]).skip((page - 1) * page_size).limit(page_size)
        return page_result([message_json(item) for item in documents], database.messages.count_documents(query), page, page_size)

    @application.post("/api/conversations/{conversation_id}/messages", response_model=Message, status_code=201, tags=["messages"])
    def send_message(conversation_id: str, payload: MessageInput, user=Depends(current_user), database=Depends(db)):
        participant_conversation(database, conversation_id, user["_id"])
        rate_limit(database, f"message:{user['_id']}", 120)
        now = utcnow()
        document = {"_id": str(uuid4()), "conversation_id": conversation_id, "sender_id": user["_id"], "body": payload.body, "created_at": now}
        database.messages.insert_one(document)
        database.conversations.update_one({"_id": conversation_id}, {"$set": {"last_message": payload.body, "updated_at": now}})
        return message_json(document)

    return application


app = create_app()
