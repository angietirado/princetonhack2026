import asyncio
import json
from datetime import date

import httpx
from fastapi.testclient import TestClient

from spending_api.agent_tools import SpendingTools
from spending_api.config import Settings
from spending_api.main import create_app
from spending_api.models import DateRange
from spending_api.nessie import collect_pages, normalize
from spending_generator.cli import build_fixture


def fixture():
    return build_fixture(42, 90, date(2026, 11, 13))


def test_generator_is_reproducible_and_scenario_changes_spending():
    baseline = fixture()
    assert baseline == fixture()
    increased = build_fixture(42, 90, date(2026, 11, 13), "overspending")
    totals = [
        SpendingTools(normalize(raw, "fixture", "cus_demo"))
        .summarize_spending(DateRange())
        .net_spending_cents
        for raw in (baseline, increased)
    ]
    assert totals[1] > totals[0]


def test_totals_exclude_transfers_pending_declined_and_subtract_refunds():
    raw = fixture()
    template = raw["transactions"][0]
    raw["transactions"] = [
        dict(template, id=str(i), kind=kind, status=status, amount_cents=amount)
        for i, (kind, status, amount) in enumerate(
            [
                ("purchase", "completed", 1234),
                ("refund", "completed", 234),
                ("fee", "completed", 100),
                ("bill_payment", "completed", 500),
                ("chargeback", "completed", 200),
                ("transfer", "completed", 99000),
                ("deposit", "completed", 99999),
                ("withdrawal", "completed", 10000),
                ("purchase", "pending", 700),
                ("purchase", "declined", 800),
                ("purchase", "cancelled", 900),
            ]
        )
    ]
    tools = SpendingTools(normalize(raw, "fixture", "cus_demo"))
    summary = tools.summarize_spending(DateRange())
    assert summary.net_spending_cents == 1800
    assert summary.included_count == 5
    assert summary.excluded_count == 6
    assert sum(row.amount_cents for row in summary.categories) == 1800


def test_customer_scope_and_monthly_recurring_candidates():
    raw = fixture()
    raw["accounts"].append(dict(raw["accounts"][0], id="acct_other", customer_id="cus_other"))
    raw["transactions"].append(dict(raw["transactions"][0], id="secret", payer_id="acct_other"))
    snapshot = normalize(raw, "fixture", "cus_demo")
    assert all(a.customer_id == "cus_demo" for a in snapshot.accounts)
    assert "secret" not in {tx.id for tx in snapshot.transactions}
    recurring = SpendingTools(snapshot).find_recurring_charges(DateRange())
    assert [(r.merchant, r.amount_cents, r.occurrences) for r in recurring] == [
        ("Stream Club", 1299, 3),
    ]


def test_paginated_nessie_reads_use_bearer_header_and_cursor():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-key"
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"items": [{"id": "first"}], "next_cursor": "next"})
        assert request.url.params["cursor"] == "next"
        return httpx.Response(200, json={"items": [{"id": "last"}], "next_cursor": None})

    async def run():
        async with httpx.AsyncClient(
            base_url="https://nessie.example",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            return await collect_pages(client, "/transactions")

    assert asyncio.run(run()) == [{"id": "first"}, {"id": "last"}]
    assert len(calls) == 2


def test_http_contract_date_validation_and_demo_token(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture()), encoding="utf-8")
    settings = Settings(_env_file=None, fixture_path=path, app_api_token="demo-token")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/spending/summary").status_code == 401
        headers = {"Authorization": "Bearer demo-token"}
        response = client.get("/api/v1/spending/summary", headers=headers)
        assert response.status_code == 200
        assert response.json()["source"] == "fixture"
        assert isinstance(response.json()["net_spending_cents"], int)
        assert (
            client.get(
                "/api/v1/spending/summary?start=2026-12-01&end=2026-11-01", headers=headers
            ).status_code
            == 422
        )
        empty = client.get("/api/v1/spending/summary?start=2030-01-01", headers=headers).json()
        assert empty["net_spending_cents"] == 0
        assert empty["categories"] == []


def test_missing_fixture_gives_actionable_error(tmp_path):
    with TestClient(
        create_app(Settings(_env_file=None, fixture_path=tmp_path / "missing"))
    ) as client:
        response = client.get("/api/v1/spending/summary")
        assert response.status_code == 503
        assert "generate-data" in response.json()["detail"]
