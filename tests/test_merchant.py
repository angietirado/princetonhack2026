from datetime import date

import pytest
from fastapi.testclient import TestClient

from spending_api.merchant import Dataset, Order, Refund, build_dashboard, csv_export, investigate
from spending_api.merchant_main import create_app


def sample():
    return Dataset(
        orders=(Order("o1", "a", date(2026, 8, 20), 1000),
                Order("o2", "a", date(2026, 9, 10), 1200),
                Order("o3", "b", date(2026, 9, 11), 800),
                Order("o4", "b", date(2026, 9, 12), 900),
                Order("o5", "c", date(2026, 10, 10), 5000),
                Order("pending", "d", date(2026, 9, 12), 99999, "pending")),
        refunds=(Refund("r1", "o1", date(2026, 9, 15), 300),),
        coverage_start=date(2026, 8, 1), coverage_end=date(2026, 11, 13))


def test_demo_reconciles_all_views_and_has_useful_segments():
    data = build_dashboard()
    metrics = data["metrics"]
    assert metrics["net_revenue_cents"] == metrics["gross_revenue_cents"] - metrics["refund_cents"]
    assert sum(row["net_revenue_cents"] for row in data["trend"]) == metrics["net_revenue_cents"]
    assert sum(row["revenue_cents"] for row in data["customers"]) == metrics["net_revenue_cents"]
    assert sum(row["revenue_cents"] for row in data["segments"]) == metrics["net_revenue_cents"]
    assert sum(row["orders"] for row in data["trend"]) == metrics["order_count"]
    assert all(row["customers"] > 0 for row in data["segments"])
    assert data["source"]["synthetic"] is True
    assert build_dashboard() == data


def test_returning_uses_prior_history_not_second_order_within_period():
    data = build_dashboard(date(2026, 9, 1), date(2026, 9, 30), sample())
    assert data["metrics"] == {"gross_revenue_cents": 2900, "refund_cents": 300,
        "net_revenue_cents": 2600, "order_count": 3, "active_customers": 2,
        "returning_customers": 1, "returning_share": 0.5, "average_order_cents": 867, "gross_average_order_cents": 967, "within_period_repeat_customers": 1, "within_period_repeat_share": 0.5}
    assert {row["id"] for row in data["customers"]} == {"a", "b"}  # future/pending excluded
    assert next(row for row in data["customers"] if row["id"] == "b")["first_seen"] == "2026-09-11"


def test_refund_only_period_preserves_negative_net_and_undefined_rates():
    data = build_dashboard(date(2026, 9, 15), date(2026, 9, 15), sample())
    assert data["metrics"]["net_revenue_cents"] == -300
    assert data["metrics"]["order_count"] == 0
    assert data["metrics"]["active_customers"] == 0
    assert data["metrics"]["returning_share"] is None
    assert data["metrics"]["average_order_cents"] is None
    assert sum(row["revenue_cents"] for row in data["customers"]) == -300


def test_cohort_future_and_incomplete_months_are_unknown():
    data = build_dashboard(date(2026, 8, 1), date(2026, 9, 15), sample())
    august = next(row for row in data["cohorts"] if row["month"] == "2026-08")
    assert august["retention"] == [1, None, None, None]
    complete = build_dashboard(date(2026, 8, 1), date(2026, 9, 30), sample())
    august = next(row for row in complete["cohorts"] if row["month"] == "2026-08")
    assert august["retention"] == [1, 1, None, None]


def test_range_bounds_and_coverage_disclosure():
    with pytest.raises(ValueError, match="Start"):
        build_dashboard(date(2026, 11, 2), date(2026, 11, 1))
    with pytest.raises(ValueError, match="366"):
        build_dashboard(date(2020, 1, 1), date(2026, 11, 1))
    data = build_dashboard(date(2026, 11, 14), date(2026, 11, 15))
    assert data["metrics"]["net_revenue_cents"] == 0
    assert any("unknown" in value for value in data["source"]["limitations"])


def test_invalid_identities_money_and_refunds_fail_closed():
    data = sample()
    with pytest.raises(ValueError, match="Duplicate"):
        build_dashboard(data=Dataset(data.orders + (data.orders[0],), data.refunds))
    with pytest.raises(ValueError, match="integer"):
        build_dashboard(data=Dataset((Order("x", "a", date(2026, 9, 1), True),), ()))
    with pytest.raises(ValueError, match="exceed"):
        build_dashboard(data=Dataset(data.orders, (Refund("too-much", "o1", date(2026, 9, 15), 1001),)))


def test_api_auth_dates_export_and_investigation(monkeypatch):
    monkeypatch.setenv("OWNER_API_TOKEN", "test-token")
    with TestClient(create_app(dataset=sample())) as client:
        assert client.get("/health").status_code == 200
        for path in ("/api/v1/owner/dashboard", "/api/v1/owner/status", "/api/v1/owner/export.csv"):
            assert client.get(path).status_code == 401
        assert client.post("/api/v1/owner/ask", json={"question": "sales"}).status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        response = client.get("/api/v1/owner/dashboard?start=2026-09-01&end=2026-09-30", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["metrics"]["net_revenue_cents"] == 2600
        assert client.get("/api/v1/owner/dashboard?start=2026-09-30&end=2026-09-01", headers=headers).status_code == 422
        exported = client.get("/api/v1/owner/export.csv?start=2026-09-01&end=2026-09-30", headers=headers)
        assert exported.status_code == 200
        assert "2026-09-01,2026-09-30,True" in exported.text
        answer = client.post("/api/v1/owner/ask", headers=headers,
                             json={"question": "How are sales?", "start": "2026-09-01", "end": "2026-09-30"})
        assert answer.status_code == 200
        assert answer.json()["provider"] == "deterministic"
        assert "$26.00" in answer.json()["answer"]
        assert client.post("/api/v1/owner/ask", headers=headers, json={"question": "  "}).status_code == 422
        assert client.post("/api/v1/owner/ask", headers=headers, json={"question": "x", "business_id": "other"}).status_code == 422


def test_csv_escapes_formula_text_and_investigator_discloses_rules():
    data = build_dashboard()
    data["customers"][0]["label"] = "=HYPERLINK(1)"
    assert "'=HYPERLINK(1)" in csv_export(data)
    answer = investigate("Who is at risk of churn?", data)
    assert "does not prove churn" in answer["answer"]
    assert answer["evidence"]
    assert investigate("Who will win the lottery?", data)["status"] == "clarification"


def test_unobserved_future_does_not_manufacture_inactivity_or_action_suggestions():
    covered = build_dashboard()
    future = build_dashboard(date(2026, 11, 15), date(2026, 12, 14))
    observed_segments = {row["id"]: row["customers"] for row in covered["segments"]}
    future_segments = {row["id"]: row["customers"] for row in future["segments"]}
    assert observed_segments["slipping"] == 23
    assert future_segments == observed_segments
    assert {row["id"]: row["days_since_last"] for row in future["customers"]} == {
        row["id"]: row["days_since_last"] for row in covered["customers"]}
    assert future["source"]["segment_as_of"] == "2026-11-13"
    assert future["actions"] == []
    assert future["metrics"]["net_revenue_cents"] == 0
    assert future["metrics"]["order_count"] == 0
    assert future["metrics"]["gross_average_order_cents"] is None
    assert future["metrics"]["within_period_repeat_share"] is None
    assert any("Later inactivity is unknown" in text for text in future["source"]["limitations"])
    assert "2026-11-13" in future["definitions"]["segments"]
    assert "later inactivity is unknown" in investigate("Who is slipping?", future)["answer"]


def test_historical_segment_cutoff_stays_at_selected_end():
    historical = build_dashboard(date(2026, 9, 1), date(2026, 9, 30), sample())
    assert historical["source"]["segment_as_of"] == "2026-09-30"
    assert next(row for row in historical["customers"] if row["id"] == "a")["days_since_last"] == 20


def test_old_refund_does_not_change_gross_average_purchase_size():
    data = Dataset(
        (Order("old", "a", date(2026, 10, 1), 100000),
         Order("new", "a", date(2026, 11, 1), 1000)),
        (Refund("refund", "old", date(2026, 11, 1), 100000),),
        coverage_start=date(2026, 10, 1), coverage_end=date(2026, 11, 1))
    metrics = build_dashboard(date(2026, 11, 1), date(2026, 11, 1), data)["metrics"]
    assert metrics["gross_average_order_cents"] == 1000
    assert metrics["average_order_cents"] == -99000
    assert metrics["net_revenue_cents"] == -99000
    assert metrics["within_period_repeat_customers"] == 0
    assert metrics["within_period_repeat_share"] == 0


def test_short_history_distinguishes_twice_in_period_from_prior_history():
    data = Dataset(tuple(Order(f"o{customer}-{day}", f"c{customer}", date(2026, 11, day), 1000)
                         for customer in range(10) for day in (1, 8)), (),
                   coverage_start=date(2026, 11, 1), coverage_end=date(2026, 11, 8))
    snapshot = build_dashboard(data=data)
    assert snapshot["metrics"]["returning_share"] == 0
    assert snapshot["metrics"]["within_period_repeat_customers"] == 10
    assert snapshot["metrics"]["within_period_repeat_share"] == 1
    assert any("No supplied purchase history precedes" in text for text in snapshot["source"]["limitations"])
    answer = investigate("Who bought twice in this period?", snapshot)
    assert "10 of 10" in answer["answer"]
    assert "earlier" in answer["answer"].lower()


def test_source_notes_survive_dashboard_and_affect_revision():
    from dataclasses import replace
    original = sample()
    notes = ("Chargebacks were excluded from this source capture.",
             "Dates follow created_at, not settlement time.")
    noted = build_dashboard(data=replace(original, source_notes=notes))
    base = build_dashboard(data=original)
    assert all(note in noted["source"]["limitations"] for note in notes)
    assert noted["source"]["revision"] != base["source"]["revision"]
    assert noted["metrics"] == base["metrics"]


@pytest.mark.parametrize("notes", [["Not an immutable tuple"], ("",), ("x" * 1001,), ("x",) * 21, (123,)])
def test_source_notes_are_bounded_text(notes):
    from dataclasses import replace
    with pytest.raises(ValueError, match="Source notes"):
        build_dashboard(data=replace(sample(), source_notes=notes))


@pytest.mark.parametrize("question", ["Which customers have stopped returning?", "Who stopped buying?"])
def test_shipped_slipping_question_uses_purchase_gap_not_returning_share(question):
    data = build_dashboard()
    answer = investigate(question, data)
    assert "23 customers" in answer["answer"]
    assert "purchase gap over 30 days" in answer["answer"]
    assert answer["evidence"][0]["id"] == "slipping"
