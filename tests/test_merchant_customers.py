"""Customer evidence reconciles with the dashboard without losing refund context."""
import hashlib
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from spending_api import merchant_main
from spending_api.merchant import Dataset, Order, Refund, build_dashboard
from spending_api.merchant_customers import (
    CustomerNotFound,
    CustomerRevisionMismatch,
    customer_detail,
)
from spending_api.merchant_import import DatasetStore, encode_dataset
from spending_api.merchant_main import create_app

START, END = date(2026, 11, 10), date(2026, 11, 11)
HEADERS = {"Authorization": "Bearer owner-test-token"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("OWNER_DATA_DIR", raising=False)
    monkeypatch.setenv("OWNER_AI_ENABLED", "false")


def sample():
    return Dataset(
        orders=(
            Order("o1", "customer_a", date(2026, 10, 1), 1000),
            Order("o2", "customer_a", date(2026, 10, 15), 2000),
            Order("o3", "customer_a", date(2026, 10, 31), 3000),
            Order("o4", "customer_a", date(2026, 11, 10), 4000),
            Order("o5", "customer_a", date(2026, 11, 20), 5000),
            Order("pending", "customer_a", date(2026, 11, 10), 99999, "pending"),
            Order("cancelled", "customer_a", date(2026, 11, 10), 88888, "cancelled"),
            Order("foreign", "customer_b", date(2026, 11, 10), 777),
        ),
        refunds=(
            Refund("r1", "o1", date(2026, 11, 10), 500),
            Refund("r2", "o4", date(2026, 11, 11), 1000),
            Refund("r3", "o4", date(2026, 11, 25), 500),
        ),
        coverage_start=date(2026, 10, 1), coverage_end=date(2026, 11, 30),
        as_of=date(2026, 12, 1), business_name="Customer evidence fixture",
    )


def test_period_and_history_reconcile_with_refunds_of_earlier_purchases():
    data = sample()
    detail = customer_detail(data, "customer_a", START, END)
    dashboard = build_dashboard(START, END, data)
    customer = next(row for row in dashboard["customers"] if row["id"] == "customer_a")
    assert detail["source"] == dashboard["source"]
    assert detail["period"] == dashboard["period"]
    assert detail["customer"] == customer
    assert detail["period_metrics"] == {
        "orders": 1, "gross_revenue_cents": 4000, "refund_cents": 1500, "net_revenue_cents": 2500,
    }
    assert detail["observed_history"] == {
        "orders": 4, "gross_revenue_cents": 10000, "refund_cents": 1500, "net_revenue_cents": 8500,
        "first_seen": "2026-10-01", "last_seen": "2026-11-10",
    }
    assert detail["segment"]["id"] == "regulars"  # four history purchases, only one in period
    assert detail["segment"]["as_of"] == "2026-11-11"
    events = {row["id"]: row for row in detail["events"]}
    assert events["r1"] == {"id": "r1", "kind": "refund", "date": "2026-11-10",
                            "amount_cents": 500, "order_id": "o1", "within_period": True}
    assert events["o1"]["within_period"] is False
    assert events["o1"]["order_id"] == "o1"
    assert {"pending", "cancelled", "foreign", "o5", "r3"}.isdisjoint(events)
    signed = lambda row: row["amount_cents"] * (-1 if row["kind"] == "refund" else 1)
    assert sum(map(signed, detail["events"])) == detail["observed_history"]["net_revenue_cents"]
    assert sum(signed(row) for row in detail["events"] if row["within_period"]) == customer["revenue_cents"]
    assert all(type(row["amount_cents"]) is int and row["amount_cents"] >= 0 for row in detail["events"])


def test_empty_and_refund_only_periods_keep_prior_customer_evidence():
    data = sample()
    empty = customer_detail(data, "customer_a", date(2026, 11, 13), date(2026, 11, 13))
    assert empty["period_metrics"] == {"orders": 0, "gross_revenue_cents": 0,
                                       "refund_cents": 0, "net_revenue_cents": 0}
    assert empty["observed_history"]["orders"] == 4
    assert len(empty["events"]) == 6
    assert not any(row["within_period"] for row in empty["events"])
    refund_only = customer_detail(data, "customer_a", END, END)
    assert refund_only["period_metrics"] == {"orders": 0, "gross_revenue_cents": 0,
                                             "refund_cents": 1000, "net_revenue_cents": -1000}
    assert refund_only["events"][0]["kind"] == "refund"
    assert refund_only["events"][0]["amount_cents"] == 1000


def test_cutoff_excludes_future_records_and_does_not_age_past_coverage():
    data = sample()
    early = customer_detail(data, "customer_a", date(2026, 10, 1), date(2026, 10, 10))
    assert early["observed_history"]["orders"] == 1
    assert [row["id"] for row in early["events"]] == ["o1"]
    with pytest.raises(CustomerNotFound):
        customer_detail(data, "customer_b", date(2026, 10, 1), date(2026, 10, 10))
    covered = customer_detail(data, "customer_a", date(2026, 11, 1), data.coverage_end)
    future = customer_detail(data, "customer_a", date(2026, 12, 1), date(2026, 12, 10))
    assert future["segment"] == covered["segment"]
    assert future["segment"]["as_of"] == "2026-11-30"
    assert future["customer"]["days_since_last"] == covered["customer"]["days_since_last"] == 10
    assert future["observed_history"] == covered["observed_history"]
    assert future["period_metrics"]["net_revenue_cents"] == 0
    assert not any(row["within_period"] for row in future["events"])


def test_equal_date_paging_is_stable_disjoint_and_complete_across_id_namespaces():
    data = sample()
    # CSV order and refund IDs have separate namespaces: a tie must retain both.
    data = replace(data, refunds=data.refunds + (Refund("o4", "o1", START, 50),))
    complete = customer_detail(data, "customer_a", START, END, limit=100)
    events, offset, totals = [], 0, []
    while offset is not None:
        page = customer_detail(data, "customer_a", START, END, offset, 2,
                               expected_revision=complete["source"]["revision"])
        assert page["pagination"]["offset"] == offset
        assert page["pagination"]["limit"] == 2
        totals.append(page["pagination"]["total"])
        events.extend(page["events"])
        offset = page["pagination"]["next_offset"]
    assert events == complete["events"]
    assert totals == [7, 7, 7, 7]
    assert len({(row["kind"], row["id"]) for row in events}) == 7
    reordered = replace(data, orders=tuple(reversed(data.orders)), refunds=tuple(reversed(data.refunds)))
    assert customer_detail(reordered, "customer_a", START, END, limit=100)["events"] == events
    exhausted = customer_detail(data, "customer_a", START, END, offset=5000)
    assert exhausted["events"] == []
    assert exhausted["pagination"]["next_offset"] is None
    assert exhausted["pagination"]["total"] == 7


@pytest.mark.parametrize("options", [{"offset": -1}, {"offset": 5001}, {"offset": True},
                                      {"limit": 0}, {"limit": 101}, {"limit": 1.5}])
def test_helper_pagination_bounds(options):
    with pytest.raises(ValueError):
        customer_detail(sample(), "customer_a", START, END, **options)


def test_unknown_customer_and_revision_mismatch_have_distinct_results():
    with pytest.raises(CustomerNotFound):
        customer_detail(sample(), "unknown", START, END)
    with pytest.raises(CustomerRevisionMismatch):
        customer_detail(sample(), "unknown", START, END, expected_revision="0" * 64)


def test_api_authorizes_binds_revision_and_reports_active_switch_before_unknown_customer(tmp_path):
    data = sample()
    with TestClient(create_app(dataset=data, token="owner-test-token", data_dir=tmp_path)) as client:
        route = "/api/v1/owner/customers/customer_a"
        assert client.get(route).status_code == 401
        params = {"start": START.isoformat(), "end": END.isoformat(), "limit": 2}
        response = client.get(route, params=params, headers=HEADERS)
        assert response.status_code == 200, response.text
        result = response.json()
        assert response.headers["cache-control"] == "no-store"
        assert result["source"]["dataset_id"] == "demo"
        assert result["pagination"] == {"offset": 0, "limit": 2, "total": 6, "next_offset": 2}
        revision = result["source"]["revision"]
        next_page = client.get(route, params={**params, "offset": 2, "expected_revision": revision}, headers=HEADERS)
        assert next_page.status_code == 200
        assert next_page.json()["source"] == result["source"]
        assert next_page.json()["period"] == result["period"]
        assert {event["id"] for event in result["events"]}.isdisjoint(event["id"] for event in next_page.json()["events"])
        replacement = replace(data, orders=(Order("other", "different_customer", START, 123),),
                              refunds=(), business_name="A different business")
        payload = encode_dataset(replacement)
        store = DatasetStore(tmp_path)
        store.add(replacement, hashlib.sha256(payload.encode()).hexdigest())
        stale = client.get(route, params={**params, "expected_revision": revision}, headers=HEADERS)
        assert stale.status_code == 409
        assert "dataset changed" in stale.json()["detail"]
        assert client.get(route, params=params, headers=HEADERS).status_code == 404


def test_api_reads_active_dataset_once_even_if_selection_changes_mid_request(tmp_path, monkeypatch):
    data = sample()
    replacement = replace(data, business_name="Different selected business")
    store = DatasetStore(tmp_path)
    key = store.add(replacement, hashlib.sha256(encode_dataset(replacement).encode()).hexdigest(), select=False)
    calls = []
    original_active = DatasetStore.active
    original_detail = merchant_main.customer_detail
    def counted_active(self):
        calls.append(1)
        return original_active(self)
    def switching_detail(*args, **kwargs):
        store.select(key)
        return original_detail(*args, **kwargs)
    with TestClient(create_app(dataset=data, token="owner-test-token", data_dir=tmp_path)) as client:
        monkeypatch.setattr(DatasetStore, "active", counted_active)
        monkeypatch.setattr(merchant_main, "customer_detail", switching_detail)
        response = client.get("/api/v1/owner/customers/customer_a", headers=HEADERS)
        assert response.status_code == 200
        assert calls == [1]
        assert response.json()["source"]["dataset_id"] == "demo"
        assert response.json()["source"]["revision"] == build_dashboard(data=data)["source"]["revision"]
        assert original_active(store)[0] == key


@pytest.mark.parametrize("query", [
    "start=0", "end=20261111", "start=2026-11-10T00:00:00", "end=9999-12-31",
    "start=2026-11-11&end=2026-11-10", "offset=-1", "offset=5001", "limit=0", "limit=101",
    "expected_revision=not-a-sha256",
])
def test_api_rejects_invalid_dates_pagination_and_revisions(query):
    with TestClient(create_app(dataset=sample(), token="owner-test-token")) as client:
        response = client.get("/api/v1/owner/customers/customer_a?" + query, headers=HEADERS)
        assert response.status_code == 422