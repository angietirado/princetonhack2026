"""Isolated API integration harness: real HTTP handlers, in-memory MongoDB."""

from contextlib import ExitStack
from dataclasses import dataclass, field
import os
from uuid import uuid4

import mongomock
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.config import Settings
from app.main import create_app


@dataclass
class Marketplace:
    app: object
    database: object
    stack: ExitStack
    clients: list[TestClient] = field(default_factory=list)

    def client(self) -> TestClient:
        client = self.stack.enter_context(TestClient(self.app))
        self.clients.append(client)
        return client

    def profile(self, email: str, name: str = "Test Student"):
        client = self.client()
        response = client.post(
            "/api/student/verify",
            json={"email": email, "display_name": name},
        )
        assert response.status_code == 200, response.text
        return client, response.json()

    def listing(self, client: TestClient, **changes):
        payload = {
            "title": "Calculus textbook",
            "description": "A clean textbook with a few helpful pencil notes. Ready for campus pickup.",
            "price_cents": 2500,
            "category": "books",
            "condition": "good",
            "pickup_location": "Main library entrance",
            "image_url": None,
        }
        payload.update(changes)
        response = client.post("/api/listings", json=payload)
        assert response.status_code == 201, response.text
        return response.json()


def make_market(*, devmode: bool):
    uri = os.getenv("TEST_MONGODB_URI")
    mongo = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True) if uri else mongomock.MongoClient(tz_aware=True)
    database_name = f"campus_test_{uuid4().hex}"
    database = mongo[database_name]
    settings = Settings(
        devmode=devmode,
        cookie_secure=False,
        allowed_origins=["http://testserver", "http://localhost:5173"],
    )
    app = create_app(settings=settings, database=database)
    try:
        with ExitStack() as stack:
            yield Marketplace(app, database, stack)
    finally:
        # Only this fixture's generated database is deleted, never an app database.
        mongo.drop_database(database_name)
        mongo.close()


@pytest.fixture
def market():
    yield from make_market(devmode=False)


@pytest.fixture
def dev_market():
    yield from make_market(devmode=True)
