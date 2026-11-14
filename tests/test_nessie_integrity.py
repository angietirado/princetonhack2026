"""Source identity and refund-scope regressions, using only synthetic records."""

import asyncio
import copy
import hashlib
import itertools
from datetime import UTC, date, datetime

import httpx
import pytest

from spending_api.agent_tools import SpendingTools
from spending_api.models import DateRange
from spending_api.nessie import DataSourceError, collect_pages, normalize
from spending_generator.cli import build_fixture


def account(identifier="a", customer="customer"):
    return {
        "id": identifier,
        "customer_id": customer,
        "nickname": "Synthetic",
        "type": "Checking",
        "balance_cents": 10_000,
    }


def transaction(identifier="purchase", **changes):
    return {
        "id": identifier,
        "payer_id": "a",
        "kind": "purchase",
        "status": "completed",
        "merchant_id": "merchant",
        "amount_cents": 2000,
        "created_at": "2026-10-31T23:59:00Z",
        **changes,
    }


def payload():
    return {
        "accounts": [account()],
        "merchants": [{"id": "merchant", "name": "Synthetic Market", "category": "groceries"}],
        "transactions": [
            transaction(),
            transaction(
                "refund",
                kind="refund",
                merchant_id=None,
                amount_cents=500,
                parent_transaction_id="purchase",
                created_at="2026-11-01T00:01:00Z",
            ),
        ],
    }


def normalize_payload(raw):
    return normalize(raw, "fixture", "customer")


def refund_from(raw):
    return next(row for row in normalize_payload(raw).transactions if row.id == "refund")


def assert_unknown_refund(row):
    assert row.merchant_id is None
    assert row.merchant == "Unknown merchant"
    assert row.category == "uncategorized"
    assert row.amount_cents == 500
    assert row.kind == "refund"
    assert row.occurred_at == datetime(2026, 11, 1, 0, 1, tzinfo=UTC)


@pytest.mark.parametrize("collection", ["accounts", "merchants", "transactions"])
@pytest.mark.parametrize("order", [(0, 1), (1, 0), (0, 1, 0), (1, 0, 1)])
def test_conflicting_duplicate_ids_fail_before_scoping_in_every_order(collection, order):
    raw = payload()
    first = raw[collection][0]
    second = copy.deepcopy(first)
    if collection == "accounts":
        second["customer_id"] = "different-customer"
    elif collection == "merchants":
        second["category"] = "different-private-category"
    else:
        second["amount_cents"] = 999_999
    raw[collection] = [copy.deepcopy((first, second)[index]) for index in order]
    original = copy.deepcopy(raw)
    with pytest.raises(DataSourceError) as error:
        normalize_payload(raw)
    assert str(error.value) == "Nessie returned invalid or conflicting record identities"
    assert "different" not in str(error.value)
    assert "999999" not in str(error.value)
    assert raw == original


@pytest.mark.parametrize("collection", ["accounts", "merchants", "transactions"])
def test_identical_duplicate_rows_deduplicate_without_changing_snapshot(collection):
    raw = payload()
    expected = normalize_payload(raw)
    duplicate = dict(reversed(list(copy.deepcopy(raw[collection][0]).items())))
    raw[collection].extend([duplicate, copy.deepcopy(duplicate)])
    assert normalize_payload(raw) == expected


@pytest.mark.parametrize("collection", ["accounts", "merchants", "transactions"])
@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_duplicate_comparison_is_type_sensitive(collection, value):
    raw = payload()
    raw[collection][0]["test_metadata"] = 1
    duplicate = copy.deepcopy(raw[collection][0])
    duplicate["test_metadata"] = value
    raw[collection].append(duplicate)
    with pytest.raises(DataSourceError):
        normalize_payload(raw)


@pytest.mark.parametrize("collection", ["accounts", "merchants", "transactions"])
@pytest.mark.parametrize("identifier", [None, "", "   ", True, 1, [], {}])
def test_invalid_raw_id_is_generic_error(collection, identifier):
    raw = payload()
    raw[collection][0]["id"] = identifier
    with pytest.raises(DataSourceError, match="invalid or conflicting record identities"):
        normalize_payload(raw)


@pytest.mark.parametrize("collection", ["accounts", "merchants", "transactions"])
def test_missing_raw_id_is_generic_error(collection):
    raw = payload()
    del raw[collection][0]["id"]
    with pytest.raises(DataSourceError):
        normalize_payload(raw)


@pytest.mark.parametrize("collection", ["accounts", "merchants", "transactions"])
@pytest.mark.parametrize("invalid", [None, {}, "records", [None]])
def test_invalid_collection_cannot_reach_partial_snapshot(collection, invalid):
    raw = payload()
    raw[collection] = invalid
    with pytest.raises(DataSourceError):
        normalize_payload(raw)


def test_foreign_duplicate_transaction_cannot_replace_owned_transaction():
    raw = payload()
    raw["accounts"].append(account("foreign", "other-customer"))
    duplicate = transaction(payer_id="foreign", amount_cents=999999)
    for rows in ([raw["transactions"][0], duplicate], [duplicate, raw["transactions"][0]]):
        candidate = copy.deepcopy(raw)
        candidate["transactions"] = rows
        with pytest.raises(DataSourceError):
            normalize_payload(candidate)


def test_conflicts_are_detected_before_invalid_transaction_payload_is_normalized():
    raw = payload()
    raw["transactions"][0]["created_at"] = "not a timestamp"
    raw["transactions"].append(transaction(amount_cents=3))
    with pytest.raises(DataSourceError, match="identities"):
        normalize_payload(raw)


def test_conflicting_duplicate_foreign_records_fail_closed_without_identifiers():
    raw = payload()
    raw["accounts"].append(account("foreign-account", "different-customer"))
    raw["transactions"].extend(
        [
            transaction("private-identifier", payer_id="foreign-account"),
            transaction("private-identifier", payer_id="foreign-account", amount_cents=123456),
        ]
    )
    with pytest.raises(DataSourceError) as error:
        normalize_payload(raw)
    assert "private" not in str(error.value)
    assert "123456" not in str(error.value)


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_same_account_refund_metadata_is_order_independent_and_uses_refund_date(order):
    raw = payload()
    raw["transactions"].append(transaction("other", amount_cents=700))
    raw["transactions"] = [raw["transactions"][index] for index in order]
    original = copy.deepcopy(raw)
    snapshot = normalize_payload(raw)
    row = next(tx for tx in snapshot.transactions if tx.id == "refund")
    assert row.merchant_id == "merchant"
    assert row.merchant == "Synthetic Market"
    assert row.category == "groceries"
    assert row.account_id == "a"
    assert row.amount_cents == 500
    assert row.occurred_at == datetime(2026, 11, 1, 0, 1, tzinfo=UTC)
    november = SpendingTools(snapshot).summarize_spending(
        DateRange(start=date(2026, 11, 1), end=date(2026, 11, 1))
    )
    assert november.net_spending_cents == -500
    assert [(item.category, item.amount_cents) for item in november.categories] == [
        ("groceries", -500)
    ]
    assert raw == original


@pytest.mark.parametrize("customer", ["customer", "foreign-customer"])
@pytest.mark.parametrize("reverse", [False, True])
def test_parent_from_another_account_does_not_supply_refund_metadata(customer, reverse):
    raw = payload()
    raw["accounts"].append(account("other-account", customer))
    raw["transactions"][0]["payer_id"] = "other-account"
    raw["merchants"][0].update(name="Private Parent Name", category="private category")
    if reverse:
        raw["transactions"].reverse()
    row = refund_from(raw)
    assert_unknown_refund(row)
    assert "Private" not in row.model_dump_json()


def test_foreign_purchase_payee_match_does_not_make_a_valid_parent():
    raw = payload()
    raw["accounts"].append(account("foreign", "foreign-customer"))
    raw["transactions"][0].update(payer_id="foreign", payee_id="a")
    assert_unknown_refund(refund_from(raw))


def test_refund_received_via_payee_can_reference_own_purchase():
    raw = payload()
    raw["transactions"][1].update(payer_id="merchant", payee_id="a")
    row = refund_from(raw)
    assert row.account_id == "a"
    assert row.merchant_id == "merchant"
    assert row.category == "groceries"


@pytest.mark.parametrize("kind", ["refund", "transfer", "deposit", "fee", "bill_payment"])
def test_nonpurchase_parent_does_not_supply_refund_metadata(kind):
    raw = payload()
    raw["transactions"][0]["kind"] = kind
    assert_unknown_refund(refund_from(raw))


@pytest.mark.parametrize("status", ["pending", "declined", "cancelled"])
def test_noncompleted_parent_does_not_supply_refund_metadata(status):
    raw = payload()
    raw["transactions"][0]["status"] = status
    assert_unknown_refund(refund_from(raw))


@pytest.mark.parametrize("parent_id", [None, "missing", "refund"])
def test_missing_or_self_parent_does_not_supply_refund_metadata(parent_id):
    raw = payload()
    raw["transactions"][1]["parent_transaction_id"] = parent_id
    assert_unknown_refund(refund_from(raw))


def test_explicit_refund_merchant_is_preserved_instead_of_parent_override():
    raw = payload()
    raw["merchants"].append({"id": "refund-merchant", "name": "Refund Label", "category": "dining"})
    raw["transactions"][1]["merchant_id"] = "refund-merchant"
    row = refund_from(raw)
    assert row.merchant_id == "refund-merchant"
    assert row.category == "dining"
    assert row.merchant == "Refund Label"


def test_parent_metadata_is_not_inherited_by_nonrefund_transactions():
    raw = payload()
    raw["transactions"][1]["kind"] = "fee"
    row = refund_from(raw)
    assert row.kind == "fee"
    assert row.merchant_id is None
    assert row.category == "uncategorized"


def test_foreign_refund_never_enters_customer_snapshot():
    raw = payload()
    raw["accounts"].append(account("foreign", "other-customer"))
    raw["transactions"][1]["payer_id"] = "foreign"
    snapshot = normalize_payload(raw)
    assert [row.id for row in snapshot.transactions] == ["purchase"]
    assert [row.id for row in snapshot.accounts] == ["a"]
    assert SpendingTools(snapshot).summarize_spending(DateRange()).net_spending_cents == 2000


def test_original_generated_fixture_is_unchanged_byte_for_byte_after_normalization():
    raw = build_fixture(42, 90, date(2026, 11, 13))
    before = copy.deepcopy(raw)
    snapshot = normalize(raw, "fixture", "cus_demo")
    # Captured from the original normalizer before this integrity fix.
    assert hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest() == (
        "26c59c342ba95ebf0fec8925fc12b1b68041a1d7bb3aa128a1ce1aa83ba207af"
    )
    assert len(snapshot.transactions) == 181
    assert SpendingTools(snapshot).summarize_spending(DateRange()).net_spending_cents == 170918
    assert raw == before


@pytest.mark.parametrize("conflict", [False, True])
def test_paginated_read_overlaps_are_checked_at_normalization(conflict):
    raw = payload()
    duplicate = copy.deepcopy(raw["transactions"][0])
    if conflict:
        duplicate["amount_cents"] = 99
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer synthetic-token"
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"items": raw["transactions"], "next_cursor": "page-2"})
        assert request.url.params["cursor"] == "page-2"
        return httpx.Response(200, json={"items": [duplicate], "next_cursor": None})

    async def read():
        async with httpx.AsyncClient(
            base_url="https://synthetic.invalid",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer synthetic-token"},
        ) as client:
            return await collect_pages(client, "/transactions")

    raw["transactions"] = asyncio.run(read())
    assert len(calls) == 2
    if conflict:
        with pytest.raises(DataSourceError):
            normalize_payload(raw)
    else:
        snapshot = normalize_payload(raw)
        assert len(snapshot.transactions) == 2
        assert SpendingTools(snapshot).summarize_spending(DateRange()).net_spending_cents == 1500
