import json
from datetime import date

from fastapi.testclient import TestClient

from spending_api.agent_tools import SpendingTools
from spending_api.config import Settings
from spending_api.dashboard import build_dashboard
from spending_api.main import create_app
from spending_api.models import DateRange
from spending_api.nessie import normalize
from spending_generator.cli import build_fixture


def raw_data():
    raw = build_fixture(42, 90, date(2026, 11, 13))
    raw["accounts"] += [
        dict(raw["accounts"][0], id="credit", type="Credit Card", balance_cents=9000),
        dict(raw["accounts"][0], id="other", customer_id="other-customer", balance_cents=999999),
    ]
    raw["transactions"].append(dict(raw["transactions"][0], id="private", payer_id="other"))
    return raw


def test_dashboard_preserves_money_invariants_and_separates_card_debt():
    tools = SpendingTools(normalize(raw_data(), "fixture", "cus_demo"))
    data = build_dashboard(tools, DateRange())
    assert data.cash_balance_cents == 225000
    assert data.credit_balance_cents == 9000
    assert data.customer_id == "cus_demo"
    assert len(data.accounts) == 2
    assert "private" not in {row.id for row in data.transactions}
    assert data.gross_spending_cents - data.refunds_cents == data.summary.net_spending_cents
    assert sum(row.amount_cents for row in data.expense_categories) == data.gross_spending_cents
    assert sum(row.net_spending_cents for row in data.monthly_spending) == data.summary.net_spending_cents
    assert data.summary.included_count + data.summary.excluded_count == len(data.transactions)


def test_dashboard_supports_refund_only_and_empty_periods():
    raw = raw_data()
    raw["transactions"] = [dict(raw["transactions"][0], kind="refund", amount_cents=321)]
    tools = SpendingTools(normalize(raw, "fixture", "cus_demo"))
    data = build_dashboard(tools, DateRange())
    assert data.gross_spending_cents == 0
    assert data.refunds_cents == 321
    assert data.summary.net_spending_cents == -321
    assert data.monthly_spending[0].net_spending_cents == -321
    empty = build_dashboard(tools, DateRange(start=date(2030, 1, 1)))
    assert empty.transactions == empty.expense_categories == empty.monthly_spending == []
    assert empty.summary.net_spending_cents == 0
    assert empty.cash_balance_cents == 225000  # Balances are current, not period totals.


def test_dashboard_route_protects_one_consistent_date_scoped_payload(tmp_path):
    fixture = tmp_path / "demo.json"
    fixture.write_text(json.dumps(raw_data()), encoding="utf-8")
    config = Settings(_env_file=None, fixture_path=fixture, app_api_token="test-token")
    with TestClient(create_app(config)) as client:
        assert client.get("/api/v1/dashboard").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        response = client.get("/api/v1/dashboard?start=2026-11-01&end=2026-11-13", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["summary"]["start"] == "2026-11-01"
        assert all("2026-11-01" <= row["occurred_at"][:10] <= "2026-11-13"
                   for row in data["transactions"])
        assert data["summary"] == client.get(
            "/api/v1/spending/summary?start=2026-11-01&end=2026-11-13", headers=headers
        ).json()
        assert client.get(
            "/api/v1/dashboard?start=2026-12-01&end=2026-11-01", headers=headers
        ).status_code == 422
