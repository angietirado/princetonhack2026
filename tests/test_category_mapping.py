"""Independent source-category expectations recorded before implementation."""

import json
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from spending_api.agent_tools import SpendingTools, bind_snapshot
from spending_api.comparison import ComparisonRequest, compare_spending
from spending_api.config import Settings
from spending_api.dashboard import build_dashboard
from spending_api.main import create_app
from spending_api.models import Account, DateRange, Snapshot, Transaction
from spending_api.nessie import normalize


def fixture():
    records = [
        ("coffee-a", "account-a", "coffee", "Corner Shop", 450, "purchase", "completed"),
        ("coffee-b", "account-b", "coffee", "Corner Shop", 650, "purchase", "pending"),
        ("cafe-dining", "account-a", "dining", "Campus Coffee", 700, "purchase", "completed"),
        ("rent-a", "account-a", "rent", "Property", 150000, "bill_payment", "completed"),
        ("grocery-a", "account-a", "groceries", "Market", 12050, "purchase", "completed"),
        ("grocery-refund", "account-a", "groceries", "Market", 2050, "refund", "completed"),
        ("unknown-zero", "account-a", "__proto__", "Unknown", 0, "fee", "cancelled"),
        ("transport-a", "account-b", "transport", "Transit", 3000, "purchase", "completed"),
        ("coffee-trim", "account-a", " coffee ", "Corner Shop", 500, "purchase", "completed"),
        ("coffee-case", "account-b", "COFFEE", "Corner Shop", 600, "purchase", "completed"),
    ]
    return SpendingTools(
        Snapshot(
            source="fixture",
            accounts=[
                Account(
                    id=identity,
                    customer_id="category-customer",
                    nickname="Checking",
                    type="Checking",
                    balance_cents=0,
                )
                for identity in ("account-a", "account-b")
            ],
            transactions=[
                Transaction(
                    id=identity,
                    account_id=account,
                    category=category,
                    merchant=merchant,
                    merchant_id="merchant-" + identity,
                    amount_cents=cents,
                    kind=kind,
                    status=status,
                    occurred_at=datetime(2026, 11, 1, 12, tzinfo=UTC),
                    description="business coffee rent: source text is not an item label",
                )
                for identity, account, category, merchant, cents, kind, status in records
            ],
        )
    )


def mapping(data):
    return data.model_dump(mode="json")["category_mapping"]


def entry(raw, category, category_label, detail=None, detail_label=None, origin="source_mapping"):
    return {
        "raw_category": raw,
        "category_id": category,
        "category_label": category_label,
        "subcategory_id": detail,
        "subcategory_label": detail_label,
        "origin": origin,
    }


def test_mapping_uses_exact_distinct_raw_keys_and_explicit_source_dictionary():
    data = build_dashboard(fixture(), DateRange())
    result = mapping(data)
    assert result["version"] == "source-categories-v1"
    assert result["entries"] == [
        entry(" coffee ", "dining", "Dining", "coffee", "Coffee"),
        entry("COFFEE", "dining", "Dining", "coffee", "Coffee"),
        entry("__proto__", "unclassified", "Unclassified", origin="unclassified"),
        entry("coffee", "dining", "Dining", "coffee", "Coffee"),
        entry("dining", "dining", "Dining"),
        entry("groceries", "groceries", "Groceries"),
        entry("rent", "housing", "Housing", "rent", "Rent"),
        entry("transport", "transport", "Transport"),
    ]
    # Ten transactions have only eight distinct raw strings; pending/cancelled rows remain visible.
    assert len(data.transactions) == 10
    assert {item["raw_category"] for item in result["entries"]} == {
        row.category for row in data.transactions
    }
    assert all("purpose" not in item for item in result["entries"])


def test_mapping_preserves_raw_records_money_and_snapshot_comparison_identity():
    tools = fixture()
    original = tools.snapshot.model_dump(mode="json")
    before_hash = bind_snapshot(tools)[1]
    periods = ComparisonRequest(
        baseline={"start": "2026-11-01", "end": "2026-11-01"},
        current={"start": "2026-11-02", "end": "2026-11-02"},
    )
    before_comparison = compare_spending(tools, periods).model_dump(mode="json")
    data = build_dashboard(tools, DateRange())
    assert data.gross_spending_cents == 167300
    assert data.refunds_cents == 2050
    assert data.summary.net_spending_cents == 165250
    assert (data.summary.included_count, data.summary.excluded_count) == (8, 2)
    assert tools.snapshot.model_dump(mode="json") == original
    assert [row.model_dump(mode="json") for row in data.transactions] == original["transactions"]
    assert bind_snapshot(tools)[1] == before_hash
    assert compare_spending(tools, periods).model_dump(mode="json") == before_comparison
    assert "category_mapping" not in tools.snapshot.model_dump(mode="json")


def test_merchant_and_description_never_supply_missing_detail_or_business_purpose():
    tools = fixture()
    dining = next(row for row in tools.snapshot.transactions if row.id == "cafe-dining")
    dining.merchant = "Campus Coffee Business Rent"
    dining.description = "coffee, rent, office expense, deductible business"
    result = mapping(build_dashboard(tools, DateRange()))
    assert next(item for item in result["entries"] if item["raw_category"] == "dining") == entry(
        "dining", "dining", "Dining"
    )
    assert all("purpose" not in item and "deductible" not in item for item in result["entries"])


def test_date_scoped_pending_unknown_and_empty_period_get_only_their_own_mapping():
    tools = fixture()
    pending = next(row for row in tools.snapshot.transactions if row.id == "coffee-b")
    pending.occurred_at = datetime(2026, 11, 2, 12, tzinfo=UTC)
    pending.category = "pending-only-category"
    data = build_dashboard(tools, DateRange(start=date(2026, 11, 2), end=date(2026, 11, 2)))
    assert [row.id for row in data.transactions] == ["coffee-b"]
    assert mapping(data)["entries"] == [
        entry("pending-only-category", "unclassified", "Unclassified", origin="unclassified")
    ]
    assert (data.summary.net_spending_cents, data.summary.included_count) == (0, 0)
    empty = build_dashboard(tools, DateRange(start=date(2026, 12, 1)))
    assert mapping(empty) == {"version": "source-categories-v1", "entries": []}


def test_mapping_order_is_deterministic_without_reordering_or_mutating_transactions():
    tools = fixture()
    expected = mapping(build_dashboard(tools, DateRange()))
    tools.snapshot.transactions.reverse()
    order = [row.id for row in tools.snapshot.transactions]
    result = build_dashboard(tools, DateRange())
    assert mapping(result) == expected
    assert [row.id for row in result.transactions] == order
    assert [row.id for row in tools.snapshot.transactions] == order


def scoped_raw():
    return {
        "schema_version": 1,
        "customer_id": "owner",
        "accounts": [
            {
                "id": "owned", "customer_id": "owner", "nickname": "Checking",
                "type": "Checking", "balance_cents": 0,
            },
            {
                "id": "foreign", "customer_id": "someone-else", "nickname": "Private",
                "type": "Checking", "balance_cents": 0,
            },
        ],
        "merchants": [
            {"id": "owned-merchant", "name": "Shop", "category": "coffee"},
            {
                "id": "foreign-merchant", "name": "Private", "category": "FOREIGN-ONLY-CATEGORY",
            },
        ],
        "transactions": [
            {
                "id": identity, "payer_id": account, "merchant_id": merchant,
                "kind": "purchase", "status": "completed", "amount_cents": 450,
                "created_at": "2026-11-01T12:00:00Z",
            }
            for identity, account, merchant in [
                ("owned-row", "owned", "owned-merchant"),
                ("foreign-row", "foreign", "foreign-merchant"),
            ]
        ],
    }


def test_mapping_cannot_reintroduce_categories_from_another_customer():
    tools = SpendingTools(normalize(scoped_raw(), "fixture", "owner"))
    data = build_dashboard(tools, DateRange())
    assert [row.id for row in data.transactions] == ["owned-row"]
    assert mapping(data)["entries"] == [entry("coffee", "dining", "Dining", "coffee", "Coffee")]
    assert "FOREIGN-ONLY-CATEGORY" not in data.model_dump_json()


def test_dashboard_api_supplies_required_mapping_under_existing_auth_and_date_scope(tmp_path):
    path = tmp_path / "category-fixture.json"
    path.write_text(json.dumps(scoped_raw()), encoding="utf-8")
    settings = Settings(_env_file=None, fixture_path=path, app_api_token="category-fixture-token")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/dashboard").status_code == 401
        headers = {"Authorization": "Bearer category-fixture-token"}
        response = client.get("/api/v1/dashboard?start=2026-11-01&end=2026-11-01", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["category_mapping"] == {
            "version": "source-categories-v1",
            "entries": [entry("coffee", "dining", "Dining", "coffee", "Coffee")],
        }
        empty = client.get("/api/v1/dashboard?start=2026-11-02&end=2026-11-02", headers=headers)
        assert empty.json()["category_mapping"]["entries"] == []
