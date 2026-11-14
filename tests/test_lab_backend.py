"""Contract, arithmetic, persistence, and isolation tests for the shopping experiment."""

import copy
import csv
import io
import itertools
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from spending_api.lab_api import create_app
from spending_api.lab_engine import evaluate, recommend
from spending_api.lab_store import LabConflict, LabStore


@pytest.fixture
def scenario():
    return {
        "id": "test-houston", "version": "v1", "name": "Test neighborhood",
        "description": "A controlled shopping experiment",
        "home": {"label": "Home", "latitude": 0.0, "longitude": 0.0, "source": "test"},
        "defaults": {
            "budget_cents": 5000, "time_budget_minutes": 100, "travel_mode": "walk", "max_stores": 2,
        },
        "basket": [
            {"id": "milk", "name": "Milk", "quantity": 2, "unit": "gallon", "category": "Dairy"},
            {"id": "bread", "name": "Bread", "quantity": 1, "unit": "loaf", "category": "Bakery"},
        ],
        "stores": [
            {"id": "near", "name": "Near", "latitude": 0.0, "longitude": 0.001,
             "shopping_minutes": 5, "prices": {"milk": 500, "bread": 500}},
            {"id": "value", "name": "Value", "latitude": 0.0, "longitude": 0.01,
             "shopping_minutes": 8, "prices": {"milk": 300, "bread": 600}},
            {"id": "bakery", "name": "Bakery", "latitude": 0.01, "longitude": 0.0,
             "shopping_minutes": 4, "prices": {"milk": 800, "bread": 100}},
        ],
        "travel": {
            "method": "modeled_distance", "road_factor": 1.0, "walk_kmh": 5.0,
            "drive_kmh": 30.0, "drive_cost_cents_per_km": 100,
            "drive_parking_minutes_per_stop": 3,
        },
        "provenance": {"prices": "Simulated", "travel": "Modeled"},
    }


@pytest.fixture
def fixture_files(tmp_path, scenario):
    source = tmp_path / "scenarios.json"
    source.write_text(json.dumps({"schema_version": 1, "scenarios": [scenario]}), encoding="utf-8")
    return source, tmp_path / "experiment.sqlite3"


@pytest.fixture
def client(fixture_files):
    source, database = fixture_files
    with TestClient(create_app(scenarios_path=source, db_path=database, admin_token="")) as value:
        yield value


def session_request(scenario, **changes):
    return {
        "request_id": str(uuid4()), "scenario_id": scenario["id"],
        "constraints": scenario["defaults"], "consent": True, "is_demo": False, **changes,
    }


def new_session(client, scenario, **changes):
    response = client.post("/api/v1/sessions", json=session_request(scenario, **changes))
    assert response.status_code == 201, response.text
    value = response.json()
    return value, {"Authorization": "Bearer " + value["token"]}


def choice_request(stage="initial", store="near", **changes):
    return {
        "request_id": str(uuid4()), "stage": stage,
        "assignments": {"milk": store, "bread": store}, **changes,
    }


def save(client, session, auth, **changes):
    response = client.post(
        f"/api/v1/sessions/{session['id']}/choices", headers=auth, json=choice_request(**changes)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_exact_quantities_and_round_trip(scenario):
    result = evaluate(scenario, {"milk": "value", "bread": "value"}, scenario["defaults"])
    assert result["basket_cost_cents"] == 1200  # Two gallons, one loaf.
    assert result["transport_cost_cents"] == 0
    assert result["distance_km"] == pytest.approx(2.2239016, rel=1e-6)
    assert result["travel_minutes"] == pytest.approx(26.686819, rel=1e-6)
    assert result["total_minutes"] == pytest.approx(34.686819, rel=1e-6)
    assert result["route"] == ["home", "value", "home"]


def test_drive_cost_and_parking_are_included(scenario):
    result = evaluate(
        scenario, {"milk": "value", "bread": "value"},
        {**scenario["defaults"], "travel_mode": "drive"},
    )
    assert result["transport_cost_cents"] == 222
    assert result["basket_cost_cents"] == 1200
    assert result["total_cost_cents"] == 1422
    assert result["shopping_minutes"] == 11
    assert result["total_minutes"] == pytest.approx(15.447803, rel=1e-6)


def test_feasibility_uses_unrounded_duration(scenario):
    assignments = {"milk": "near", "bread": "near"}
    full = evaluate(scenario, assignments, scenario["defaults"])
    exact = {**scenario["defaults"], "time_budget_minutes": full["total_minutes"]}
    assert evaluate(scenario, assignments, exact)["feasible"]
    below = {**exact, "time_budget_minutes": full["total_minutes"] - 0.00001}
    assert not evaluate(scenario, assignments, below)["feasible"]


def test_constraints_are_not_silently_relaxed(scenario):
    constraints = {**scenario["defaults"], "budget_cents": 1, "time_budget_minutes": 0.1}
    result = recommend(scenario, constraints)
    assert result["status"] == "no_feasible_plan"
    assert result["plans"] == []
    assert result["feasible_plan_count"] == 0
    assert result["evaluated_plan_count"] > 0


def test_two_store_route_counts_return_and_both_shops(scenario):
    result = evaluate(scenario, {"milk": "value", "bread": "bakery"}, scenario["defaults"])
    assert result["basket_cost_cents"] == 700
    assert result["shopping_minutes"] == 12
    assert len(result["route"]) == 4
    assert result["route"][0] == result["route"][-1] == "home"
    assert result["distance_km"] > 3.7
    result = evaluate(
        scenario, {"milk": "value", "bread": "bakery"}, {**scenario["defaults"], "max_stores": 1}
    )
    assert result["feasible"] is False


def test_recommendations_match_exhaustive_assignment_frontier(scenario):
    stores = [store["id"] for store in scenario["stores"]]
    all_plans = [
        evaluate(scenario, dict(zip(["milk", "bread"], assignment)), scenario["defaults"])
        for assignment in itertools.product(stores, repeat=2)
    ]
    pairs = {(plan["total_cost_cents"], plan["total_minutes"]) for plan in all_plans if plan["feasible"]}
    frontier = {
        point for point in pairs
        if not any(other != point and other[0] <= point[0] and other[1] <= point[1] for other in pairs)
    }
    result = recommend(scenario, scenario["defaults"])
    assert {(plan["total_cost_cents"], plan["total_minutes"]) for plan in result["plans"]} == frontier
    assert len({plan["id"] for plan in result["plans"]}) == len(result["plans"])


@pytest.mark.parametrize("assignments", [
    {}, {"milk": "near"}, {"milk": "near", "bread": "missing"},
    {"milk": "near", "bread": "near", "extra": "near"},
])
def test_incomplete_or_unknown_basket_rejected(scenario, assignments):
    with pytest.raises(ValueError):
        evaluate(scenario, assignments, scenario["defaults"])


def test_catalog_and_empty_analytics_are_honest(client):
    assert client.get("/health").json() == {"status": "ok", "app": "blockwise"}
    catalog = client.get("/api/v1/scenarios")
    assert "simulated" in catalog.json()["simulation_notice"]
    assert catalog.headers["cache-control"] == "no-store"
    summary = client.get("/api/v1/analytics/summary").json()
    assert summary["session_count"] == 0
    assert summary["mean_savings_cents"] is None
    assert summary["tradeoffs"] == []


def test_full_participant_flow_and_session_isolation(client, scenario):
    session, auth = new_session(client, scenario)
    path = f"/api/v1/sessions/{session['id']}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(path + "/recommendations", headers=auth, json={}).status_code == 409
    assert client.post(path + "/choices", headers=auth, json=choice_request("revised")).status_code == 409
    preview = client.post(path + "/evaluate", headers=auth, json={"assignments": {"milk": "near", "bread": "near"}})
    assert preview.status_code == 200
    assert client.get(path, headers=auth).json()["choices"] == []
    initial = save(client, session, auth)
    recommendations = client.post(path + "/recommendations", headers=auth, json={})
    assert recommendations.status_code == 200
    plan = recommendations.json()["plans"][0]
    revised = save(client, session, auth, stage="revised", assignments=plan["assignments"])
    assert revised["evaluation"]["total_cost_cents"] < initial["evaluation"]["total_cost_cents"]
    recovered = client.get(path, headers=auth).json()
    assert len(recovered["choices"]) == 2
    assert "token" not in recovered
    other, other_auth = new_session(client, scenario)
    assert client.get(path, headers=other_auth).status_code == 401
    assert other["id"] != session["id"]


def test_session_and_choice_retries_are_idempotent(client, scenario):
    request = session_request(scenario)
    first = client.post("/api/v1/sessions", json=request).json()
    retry = client.post("/api/v1/sessions", json=request).json()
    assert first == retry
    assert client.post("/api/v1/sessions", json={**request, "is_demo": True}).status_code == 409
    auth = {"Authorization": "Bearer " + first["token"]}
    path = f"/api/v1/sessions/{first['id']}/choices"
    body = choice_request()
    initial = client.post(path, json=body, headers=auth).json()
    repeated = client.post(path, json=body, headers=auth).json()
    assert initial == repeated
    assert client.post(path, json={**body, "stage": "revised"}, headers=auth).status_code == 409
    assert client.post(path, json=choice_request(), headers=auth).status_code == 409


def test_concurrent_duplicate_sessions_and_initial_choices_are_unique(tmp_path, scenario):
    store = LabStore(tmp_path / "concurrency.sqlite3")
    body = session_request(scenario)
    with ThreadPoolExecutor(max_workers=6) as pool:
        sessions = list(pool.map(lambda _: store.create_session(body, scenario), range(12)))
    assert len({session["id"] for session in sessions}) == 1
    evaluation = evaluate(scenario, {"milk": "near", "bread": "near"}, scenario["defaults"])
    choice = choice_request()
    with ThreadPoolExecutor(max_workers=6) as pool:
        choices = list(pool.map(
            lambda _: store.add_choice(sessions[0]["id"], choice, evaluation), range(12)
        ))
    assert len({item["id"] for item in choices}) == 1
    with pytest.raises(LabConflict):
        store.add_choice(sessions[0]["id"], choice_request(), evaluation)


def test_demo_separation_and_changed_constraint_pair_exclusion(client, scenario):
    participant, auth = new_session(client, scenario)
    save(client, participant, auth)
    save(client, participant, auth, stage="revised", store="value")
    changed, changed_auth = new_session(client, scenario)
    save(client, changed, changed_auth)
    save(
        client, changed, changed_auth, stage="revised", store="value",
        constraints={**scenario["defaults"], "budget_cents": 6000},
    )
    demo, demo_auth = new_session(client, scenario, is_demo=True)
    save(client, demo, demo_auth)
    summary = client.get("/api/v1/analytics/summary").json()
    assert summary["session_count"] == 2
    assert summary["matched_pair_count"] == 1
    assert summary["mean_savings_cents"] == 300
    assert summary["initial_choice_count"] == 2
    assert summary["revised_choice_count"] == 2
    assert client.get("/api/v1/analytics/summary?data_mode=demo").json()["session_count"] == 1
    assert client.get("/api/v1/analytics/summary?data_mode=all").json()["session_count"] == 3
    summaries = client.get("/api/v1/analytics/sessions").json()["sessions"]
    assert sum(item["comparable"] for item in summaries) == 1


def test_admin_auth_and_no_token_leak(fixture_files, scenario):
    source, database = fixture_files
    with TestClient(create_app(scenarios_path=source, db_path=database, admin_token="test-admin")) as client:
        session, auth = new_session(client, scenario)
        save(client, session, auth)
        admin = {"Authorization": "Bearer test-admin"}
        paths = [
            "/api/v1/analytics/summary", "/api/v1/analytics/sessions",
            "/api/v1/analytics/export", f"/api/v1/analytics/sessions/{session['id']}",
        ]
        for path in paths:
            assert client.get(path).status_code == 401
            assert client.get(path, headers=auth).status_code == 401
            response = client.get(path, headers=admin)
            assert response.status_code == 200
            assert session["token"] not in response.text
            assert '"token"' not in response.text
            assert "request_id" not in response.text


def test_session_snapshot_survives_catalog_changes(fixture_files, scenario):
    source, database = fixture_files
    with TestClient(create_app(scenarios_path=source, db_path=database, admin_token="")) as client:
        session, auth = new_session(client, scenario)
    changed = copy.deepcopy(scenario)
    changed["version"] = "v2"
    changed["stores"][0]["prices"]["milk"] = 9999
    source.write_text(json.dumps({"schema_version": 1, "scenarios": [changed]}), encoding="utf-8")
    with TestClient(create_app(scenarios_path=source, db_path=database, admin_token="")) as client:
        recovered = client.get(f"/api/v1/sessions/{session['id']}", headers=auth).json()
        assert recovered["scenario"]["version"] == "v1"
        assert recovered["scenario_hash"] == session["scenario_hash"]
        initial = save(client, session, auth)
        assert initial["evaluation"]["basket_cost_cents"] == 1500


@pytest.mark.parametrize("changes", [
    {"consent": False}, {"consent": "true"}, {"is_demo": 1}, {"request_id": "not-a-uuid"},
    {"constraints": {"budget_cents": True, "time_budget_minutes": 30, "travel_mode": "walk", "max_stores": 1}},
    {"constraints": {"budget_cents": 1000, "time_budget_minutes": "30", "travel_mode": "walk", "max_stores": 1}},
    {"constraints": {"budget_cents": 1000, "time_budget_minutes": 30, "travel_mode": "fly", "max_stores": 1}},
])
def test_invalid_inputs_rejected_without_recording(client, scenario, changes):
    response = client.post("/api/v1/sessions", json=session_request(scenario, **changes))
    assert response.status_code == 422
    assert client.get("/api/v1/analytics/summary").json()["session_count"] == 0


def test_request_limits_and_ambiguous_json(client):
    headers = {"Content-Type": "application/json"}
    assert client.post("/api/v1/sessions", content="x" * 16385, headers=headers).status_code == 413
    assert client.post("/api/v1/sessions", content='{"consent":true,"consent":false}', headers=headers).status_code == 422
    assert client.post("/api/v1/sessions", content='{"amount":NaN}', headers=headers).status_code == 422
    assert client.post("/api/v1/sessions", content="hello").status_code == 415


def test_pagination_filter_and_csv_formula_safety(fixture_files, scenario):
    source, database = fixture_files
    scenario["name"] = '=HYPERLINK("https://invalid.example")'
    source.write_text(json.dumps({"schema_version": 1, "scenarios": [scenario]}), encoding="utf-8")
    with TestClient(create_app(scenarios_path=source, db_path=database, admin_token="")) as client:
        for _ in range(3):
            session, auth = new_session(client, scenario)
            save(client, session, auth)
        first = client.get("/api/v1/analytics/sessions?limit=2").json()
        second = client.get("/api/v1/analytics/sessions?limit=2&offset=2").json()
        assert first["total"] == second["total"] == 3
        assert len(first["sessions"]) == 2 and len(second["sessions"]) == 1
        assert client.get("/api/v1/analytics/summary?scenario_id=absent").json()["session_count"] == 0
        assert client.get("/api/v1/analytics/sessions?limit=101").status_code == 422
        response = client.get("/api/v1/analytics/export")
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 3
        assert rows[0]["scenario_name"].startswith("'=")
        assert rows[0]["total_cost_cents"] == "1500"
        assert "attachment" in response.headers["content-disposition"]

