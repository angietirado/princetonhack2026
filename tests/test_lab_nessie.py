"""Nessie v2 integration tests use mocked HTTP only; no bank credentials are loaded."""

import asyncio
import copy
import json
from decimal import Decimal

import httpx
import pytest

from spending_api.lab_nessie import (
    NESSIE_BASE_URL,
    NessieReadClient,
    NessieReadError,
    dollars_to_cents,
    normalize_nessie_snapshot,
)
from spending_api.lab_wallet import WalletDataError, project_wallet

CAPTURED = "2026-11-14T00:00:00Z"


@pytest.fixture
def raw():
    return {
        "accounts": [
            {"id": "checking", "customer_id": "customer", "type": "Checking", "nickname": "Checking",
             "balance_cents": 100000, "pending_debits_cents": 5000, "available_cents": 95000,
             "account_number": "fictional-account-number", "rewards": 0,
             "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-13T00:00:00Z"},
            {"id": "card", "customer_id": "customer", "type": "Credit Card", "nickname": "Card",
             "balance_cents": 30000, "pending_debits_cents": 0, "available_cents": 20000,
             "credit_limit_cents": 50000, "account_number": "fictional-card-number", "rewards": 0,
             "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-13T00:00:00Z"},
        ],
        "bills": [{
            "id": "utility", "account_id": "checking", "status": "pending", "payee": "Utility",
            "creation_date": "2026-11-01", "upcoming_payment_date": "2026-11-16",
            "payment_amount": 12.50, "created_at": "2026-11-01T00:00:00Z",
            "updated_at": "2026-11-13T00:00:00Z",
        }],
        "transactions": [
            {"id": "hold", "kind": "purchase", "status": "pending", "medium": "balance",
             "amount_cents": 5000, "payer_id": "checking", "created_at": "2026-11-13T00:00:00Z",
             "updated_at": "2026-11-13T00:00:00Z", "settle_at": "2026-11-14T00:00:00Z"},
            {"id": "past-deposit", "kind": "deposit", "status": "completed", "medium": "balance",
             "amount_cents": 100000, "payer_id": "checking", "created_at": "2026-11-10T00:00:00Z",
             "updated_at": "2026-11-10T00:00:00Z", "settle_at": "2026-11-10T00:00:00Z"},
            {"id": "foreign", "kind": "purchase", "status": "completed", "medium": "balance",
             "amount_cents": 999999, "payer_id": "foreign-account",
             "created_at": "2026-11-12T00:00:00Z"},
        ],
    }


def normalized(raw):
    return normalize_nessie_snapshot(raw, customer_id="customer", captured_at=CAPTURED)


def reader(raw, override=None, **options):
    requests = []
    calls = {}

    def handler(request):
        requests.append(request)
        path = request.url.path
        calls[path] = calls.get(path, 0) + 1
        if override:
            result = override(request, calls[path])
            if result is not None:
                return result
        if path == "/customers/customer/accounts":
            return httpx.Response(200, json={"items": raw["accounts"]})
        if path == "/accounts/checking/bills":
            return httpx.Response(200, json={"items": raw["bills"]})
        if path == "/accounts/card/bills":
            return httpx.Response(200, json={"items": []})
        if path == "/transactions":
            return httpx.Response(200, json={"items": raw["transactions"]})
        raise AssertionError("Unexpected endpoint")
    return NessieReadClient(
        api_key="offline-test-key", customer_id="customer",
        transport=httpx.MockTransport(handler), **options,
    ), requests


def test_current_v2_units_and_cash_semantics(raw):
    snapshot = normalized(raw)
    assert snapshot["source"] == "nessie"
    assert snapshot["available_cents"] == 95000
    assert snapshot["obligations"][0]["amount_cents"] == 1250
    assert snapshot["obligations"][0]["due_at"] == "2026-11-16T00:00:00Z"
    assert {item["id"] for item in snapshot["transactions"]} == {"hold", "past-deposit"}
    assert "account_number" not in json.dumps(snapshot)
    assert "credit_limit_cents" not in json.dumps(snapshot)
    result = project_wallet(snapshot)
    assert result["baseline_available_cents"] == 95000
    assert result["expected_income_cents"] == 0
    assert result["scheduled_obligations_cents"] == 1250
    assert result["shopping_allowance_cents"] == 93750


@pytest.mark.parametrize(("dollars", "cents"), [
    (12.5, 1250), (0.29, 29), (10, 1000), (0, 0), (Decimal("19.99"), 1999),
])
def test_dollar_conversion_is_exact(dollars, cents):
    assert dollars_to_cents(dollars) == cents


@pytest.mark.parametrize("value", [True, "12.50", None, -1, float("nan"), float("inf"), 12.345])
def test_invalid_or_subcent_dollars_fail_closed(value):
    with pytest.raises(NessieReadError):
        dollars_to_cents(value)


@pytest.mark.parametrize("field", ["available_cents", "balance_cents", "pending_debits_cents"])
def test_financial_account_fields_are_required_integer_cents(raw, field):
    raw["accounts"][0][field] = 100.0
    with pytest.raises(NessieReadError):
        normalized(raw)


def test_missing_cash_is_not_fabricated_from_other_fields(raw):
    del raw["accounts"][0]["available_cents"]
    with pytest.raises(NessieReadError):
        normalized(raw)


def test_customer_scope_and_bill_scope_are_enforced(raw):
    foreign = copy.deepcopy(raw)
    foreign["accounts"][0]["customer_id"] = "other-customer"
    with pytest.raises(NessieReadError, match="cross_customer_account"):
        normalized(foreign)
    foreign = copy.deepcopy(raw)
    foreign["bills"][0]["account_id"] = "other-account"
    with pytest.raises(NessieReadError, match="cross_customer_bill"):
        normalized(foreign)


def test_own_incoming_transfer_is_kept_but_other_customers_are_excluded(raw):
    raw["transactions"].append({
        "id": "incoming", "kind": "transfer", "status": "completed", "medium": "balance",
        "amount_cents": 3000, "payer_id": "foreign-account", "payee_id": "checking",
        "created_at": "2026-11-13T00:00:00Z",
    })
    snapshot = normalized(raw)
    assert "incoming" in {item["id"] for item in snapshot["transactions"]}
    assert "foreign" not in {item["id"] for item in snapshot["transactions"]}
    assert project_wallet(snapshot)["expected_income_cents"] == 0


def test_duplicate_overlaps_are_deduplicated_and_conflicts_fail(raw):
    raw["transactions"].append(copy.deepcopy(raw["transactions"][0]))
    assert len(normalized(raw)["transactions"]) == 2
    raw["transactions"][-1]["amount_cents"] = 5000.0
    with pytest.raises(NessieReadError, match="conflicting_duplicate"):
        normalized(raw)


def test_snapshot_is_stable_under_input_order_and_copied_from_raw(raw):
    first = normalized(raw)
    raw["accounts"].reverse()
    raw["transactions"].reverse()
    assert normalized(raw) == first
    raw["bills"][0]["payment_amount"] = 999
    assert first["obligations"][0]["amount_cents"] == 1250


def test_blank_bill_date_stays_unknown_without_monthly_guess(raw):
    raw["bills"][0].update(status="recurring", recurring_date=16, upcoming_payment_date="")
    snapshot = normalized(raw)
    assert snapshot["obligations"][0]["due_at"] is None
    result = project_wallet(snapshot)
    assert result["status"] == "needs_review"
    assert result["shopping_allowance_cents"] is None


def test_naive_and_future_transaction_timestamps_fail_closed(raw):
    raw["transactions"][0]["created_at"] = "2026-11-13T00:00:00"
    with pytest.raises(WalletDataError):
        normalized(raw)
    raw["transactions"][0]["created_at"] = "2026-11-15T00:00:00Z"
    with pytest.raises(WalletDataError):
        normalized(raw)


def test_adapter_only_gets_fixed_https_origin_and_checks_accounts_twice(raw):
    client, requests = reader(raw)
    snapshot = asyncio.run(client.capture())
    assert snapshot["source"] == "nessie"
    assert snapshot["available_cents"] == 95000
    assert all(request.method == "GET" for request in requests)
    assert all(str(request.url).startswith(NESSIE_BASE_URL + "/") for request in requests)
    assert all(request.headers["authorization"] == "Bearer offline-test-key" for request in requests)
    assert sum(request.url.path == "/customers/customer/accounts" for request in requests) == 2
    assert "offline-test-key" not in json.dumps(snapshot)


def test_transaction_pagination_and_duplicate_page_overlap(raw):
    own = raw["transactions"][0]
    other = raw["transactions"][1]

    def override(request, _count):
        if request.url.path == "/transactions":
            if request.url.params.get("cursor") == "next-page":
                return httpx.Response(200, json={"items": [own, other]})
            return httpx.Response(200, json={"items": [own], "next_cursor": "next-page"})
    client, requests = reader(raw, override)
    snapshot = asyncio.run(client.capture())
    assert len(snapshot["transactions"]) == 2
    tx_requests = [request for request in requests if request.url.path == "/transactions"]
    assert len(tx_requests) == 2
    assert tx_requests[1].url.params["cursor"] == "next-page"


def test_repeated_cursor_never_returns_partial_snapshot(raw):
    def override(request, _count):
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"items": [], "next_cursor": "repeat"})
    client, _ = reader(raw, override)
    with pytest.raises(NessieReadError, match="repeated_cursor"):
        asyncio.run(client.capture())


def test_page_limit_never_returns_partial_snapshot(raw):
    def override(request, count):
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"items": [], "next_cursor": f"page-{count}"})
    client, _ = reader(raw, override, max_pages=1)
    with pytest.raises(NessieReadError, match="page_limit"):
        asyncio.run(client.capture())


def test_record_limit_fails_closed(raw):
    client, _ = reader(raw, max_records=1)
    with pytest.raises(NessieReadError, match="record_limit"):
        asyncio.run(client.capture())


def test_cross_customer_account_stops_before_followup_reads(raw):
    raw["accounts"][0]["customer_id"] = "other"
    client, requests = reader(raw)
    with pytest.raises(NessieReadError, match="cross_customer_account"):
        asyncio.run(client.capture())
    assert len(requests) == 1


def test_cross_account_bill_response_is_rejected(raw):
    raw["bills"][0]["account_id"] = "card"
    client, _ = reader(raw)
    with pytest.raises(NessieReadError, match="cross_account_bill"):
        asyncio.run(client.capture())


def test_changed_cash_during_capture_is_rejected(raw):
    def override(request, count):
        if request.url.path == "/customers/customer/accounts" and count == 2:
            changed = copy.deepcopy(raw["accounts"])
            changed[0]["balance_cents"] -= 100
            changed[0]["available_cents"] -= 100
            return httpx.Response(200, json={"items": changed})
    client, _ = reader(raw, override)
    with pytest.raises(NessieReadError, match="accounts_changed_during_capture"):
        asyncio.run(client.capture())


@pytest.mark.parametrize("status", [302, 401, 403, 404, 429, 500])
def test_http_failures_do_not_follow_redirects_or_fall_back_to_fixtures(raw, status):
    def override(_request, _count):
        return httpx.Response(status, headers={"Location": "https://evil.invalid/steal"},
                              text="private upstream detail offline-test-key")
    client, requests = reader(raw, override)
    with pytest.raises(NessieReadError) as error:
        asyncio.run(client.capture())
    assert len(requests) == 1
    assert "offline-test-key" not in str(error.value)
    assert "private upstream detail" not in str(error.value)


@pytest.mark.parametrize("page", [{}, {"items": {}}, {"items": [], "next_cursor": {}}, {"items": [], "next_cursor": 1}])
def test_malformed_pages_do_not_become_empty_cash_snapshots(raw, page):
    client, _ = reader(raw, lambda _request, _count: httpx.Response(200, json=page))
    with pytest.raises(NessieReadError):
        asyncio.run(client.capture())


def test_duplicate_json_fields_are_rejected(raw):
    client, _ = reader(raw, lambda _request, _count: httpx.Response(
        200, content=b'{"items":[],"items":[]}', headers={"content-type": "application/json"},
    ))
    with pytest.raises(NessieReadError, match="invalid_json"):
        asyncio.run(client.capture())


def test_oversized_response_is_bounded(raw):
    client, _ = reader(raw, lambda _request, _count: httpx.Response(
        200, content=b"x" * (2 * 1024 * 1024 + 1),
    ))
    with pytest.raises(NessieReadError, match="response_size_limit"):
        asyncio.run(client.capture())


def test_total_capture_deadline():
    async def handler(_request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"items": []})
    client = NessieReadClient(
        api_key="offline-key", customer_id="customer", transport=httpx.MockTransport(handler),
        total_timeout_seconds=0.01,
    )
    with pytest.raises(NessieReadError, match="timeout"):
        asyncio.run(client.capture())


@pytest.mark.parametrize("customer_id", ["", "../other", "customer?key=bad", "https://evil.invalid", "customer/other"])
def test_customer_configuration_cannot_be_a_path_or_url(customer_id):
    with pytest.raises(NessieReadError):
        NessieReadClient(api_key="offline-key", customer_id=customer_id)

