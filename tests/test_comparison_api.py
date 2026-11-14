"""Direct comparisons remain available without model credentials or invocations."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from spending_api.config import Settings
from spending_api.main import create_app
from spending_api.models import Account, Snapshot, Transaction

AUTH = {"Authorization": "Bearer comparison-test"}
PERIODS = {
    "baseline": {"start": "2026-11-01", "end": "2026-11-07"},
    "current": {"start": "2026-11-08", "end": "2026-11-14"},
}


def config():
    return Settings(_env_file=None, data_source="fixture", app_api_token="comparison-test")


def snapshot():
    def tx(identity, day, cents, kind="purchase", status="completed"):
        return Transaction(
            id=identity,
            account_id="a",
            merchant="Cafe",
            merchant_id="m",
            category="dining",
            occurred_at=datetime(2026, 11, day, tzinfo=UTC),
            amount_cents=cents,
            kind=kind,
            status=status,
        )

    return Snapshot(
        source="fixture",
        accounts=[
            Account(id="a", customer_id="c", nickname="Test", type="Checking", balance_cents=10000)
        ],
        transactions=[
            tx("b", 2, 3000),
            tx("br", 3, 500, "refund"),
            tx("c", 9, 5500),
            tx("cr", 10, 1000, "refund"),
            tx("p", 11, 1100, status="pending"),
        ],
    )


def test_direct_comparison_has_exact_figures_and_no_model_dependency(monkeypatch):
    async def load(_):
        return snapshot()

    async def no_model(*args, **kwargs):
        pytest.fail("Direct comparison invoked a model")

    monkeypatch.setattr("spending_api.main.load_snapshot", load)
    monkeypatch.setattr("spending_api.main.answer_spending", no_model)
    with TestClient(create_app(config())) as client:
        response = client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
        assert response.status_code == 200
        result = response.json()
        assert result["total"]["baseline_cents"] == 2500
        assert result["total"]["current_cents"] == 4500
        assert result["total"]["delta_cents"] == 2000
        assert result["total"]["percent_change_bps"] == 8000
        assert result["current"]["excluded_count"] == 1
        assert result["equal_duration"] is True
        assert result["findings"] and len(result["source_hash"]) == 64
        assert result["coverage"] == "observed_records_only"
        assert response.headers["cache-control"] == "no-store"
        schema = client.get("/openapi.json").json()["paths"]["/api/v1/spending/compare"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]
        assert "properties" in schema["properties"]["baseline"]
        assert schema["properties"]["current"]["properties"]["start"]["format"] == "date"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**PERIODS, "customer_id": "another"},
        {**PERIODS, "current": {"start": "2026-11-07", "end": "2026-11-14"}},
        {**PERIODS, "baseline": {"start": "2026-11-07", "end": "2026-11-01"}},
        {**PERIODS, "current": {"start": "2026-11-08T00:00:00Z", "end": "2026-11-14"}},
    ],
)
def test_invalid_comparison_filters_never_read_data(monkeypatch, payload):
    async def forbidden(_):
        pytest.fail("Invalid filters loaded data")

    monkeypatch.setattr("spending_api.main.load_snapshot", forbidden)
    with TestClient(create_app(config())) as client:
        assert (
            client.post("/api/v1/spending/compare", headers=AUTH, json=payload).status_code == 422
        )


def test_comparison_auth_precedes_body_and_data(monkeypatch):
    async def forbidden(_):
        pytest.fail("Unauthorized comparison loaded data")

    monkeypatch.setattr("spending_api.main.load_snapshot", forbidden)
    with TestClient(create_app(config())) as client:
        assert client.post("/api/v1/spending/compare", content="invalid").status_code == 401
        assert (
            client.post(
                "/api/v1/spending/compare?customer=other", headers=AUTH, json=PERIODS
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/spending/compare",
                headers={**AUTH, "Content-Type": "application/json"},
                content=" " * 8193,
            ).status_code
            == 413
        )


def test_busy_comparison_does_not_start_another_snapshot_read(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def slow(_):
            calls.append(True)
            entered.set()
            await release.wait()
            return snapshot()

        monkeypatch.setattr("spending_api.main.load_snapshot", slow)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(config())), base_url="http://test"
        ) as client:
            first = asyncio.create_task(
                client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
            )
            await entered.wait()
            second = await client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
            assert second.status_code == 429 and calls == [True]
            release.set()
            assert (await first).status_code == 200

    asyncio.run(scenario())
