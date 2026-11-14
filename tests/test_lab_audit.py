"""Audits count initial records once and keep resource contexts separate."""

import copy
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from spending_api.lab_audit import audit_choices, register_audit_routes
from spending_api.lab_engine import evaluate
from spending_api.lab_store import LabStore
from spending_api.lab_wallet import make_fixture_wallets


@pytest.fixture
def world():
    return {
        "id": "audit", "version": "v1", "world_version": "prices-v1", "name": "Audit world", "description": "A controlled world",
        "home": {"label": "Home", "latitude": 0, "longitude": 0},
        "defaults": {"budget_cents": 1200, "time_budget_minutes": 40, "travel_mode": "walk", "max_stores": 1},
        "basket": [{"id": "milk", "name": "Milk", "quantity": 2, "unit": "gallon", "category": "Dairy"}],
        "stores": [
            {"id": "near", "name": "Near", "latitude": 0, "longitude": 0.001,
             "shopping_minutes": 5, "prices": {"milk": 500}},
            {"id": "cheaper", "name": "Cheaper", "latitude": 0, "longitude": 0.001,
             "shopping_minutes": 5, "prices": {"milk": 400}},
            {"id": "far", "name": "Far", "latitude": 0, "longitude": 0.01,
             "shopping_minutes": 5, "prices": {"milk": 300}},
        ],
        "travel": {"method": "modeled_distance", "road_factor": 1, "walk_kmh": 5,
                   "drive_kmh": 30, "drive_cost_cents_per_km": 25, "drive_parking_minutes_per_stop": 3},
        "provenance": {"prices": "Simulated", "travel": "Modeled"},
    }


@pytest.fixture
def store(tmp_path):
    return LabStore(tmp_path / "audit.sqlite3")


def add_session(store, world, *, constraints=None, selected="near", is_demo=False, initial=True):
    constraints = constraints or world["defaults"]
    session = store.create_session({"request_id": str(uuid4()), "constraints": constraints, "is_demo": is_demo}, world)
    if initial:
        add_choice(store, session, world, constraints, selected)
    return session


def add_choice(store, session, world, constraints, selected, stage="initial", identity=None):
    request = {"request_id": identity or str(uuid4()), "stage": stage}
    return store.add_choice(session["id"], request, evaluate(world, {"milk": selected}, constraints))


def with_wallet(world, *, reserve=0, broken=False):
    result = copy.deepcopy(world)
    result["financial_context"] = {
        "profile_name": "Controlled wallet", "profile_id": "steady", "snapshot": make_fixture_wallets()[0],
        "projection_inputs": {"days": 7, "reserve_cents": reserve, "expected_income": [], "events": []},
        "projection": {"spendable_now_cents": 999999999},
    }
    if broken:
        result["financial_context"]["snapshot"]["available_cents"] += 1
    return result


def one_cohort(report):
    assert len(report["cohorts"]) == 1
    return report["cohorts"][0]


def test_selected_vs_other_plan_and_matched_dominance_are_distinct(store, world):
    constraints = {**world["defaults"], "budget_cents": 900, "time_budget_minutes": 20}
    cheap = add_session(store, world, constraints=constraints, selected="cheaper")
    expensive = add_session(store, world, constraints=constraints, selected="near")
    too_slow = add_session(store, world, constraints=constraints, selected="far")
    report = audit_choices(store)
    cohort = one_cohort(report)
    assert cohort["counts"] == {
        "initial_choices": 3, "selected_plan_fits": 1, "other_plan_fits": 2,
        "no_plan_fits": 0, "needs_review": 0, "newly_feasible_under_probe": 0, "modeled_dominated": 0,
    }
    assert cohort["denominators"]["dominance_eligible_initial_choices"] == 1
    assert cohort["example_ids"]["selected_plan_fits"] == [cheap["id"]]
    assert set(cohort["example_ids"]["other_plan_fits"]) == {expensive["id"], too_slow["id"]}
    assert report["analyzed_sessions"] == 3 and not report["truncated"]


def test_dominance_requires_no_worse_cost_and_time_and_one_strict_improvement(store, world):
    expensive = add_session(store, world, selected="near")
    add_session(store, world, selected="cheaper")
    add_session(store, world, selected="far")
    cohort = one_cohort(audit_choices(store))
    assert cohort["counts"]["selected_plan_fits"] == 3
    assert cohort["counts"]["modeled_dominated"] == 1
    assert cohort["example_ids"]["modeled_dominated"] == [expensive["id"]]
    assert cohort["denominators"]["dominance_eligible_initial_choices"] == 3


@pytest.mark.parametrize("budget,time,expected", [(0, 0, 0), (99, 0, 0), (100, 0, 1), (0, 11, 0), (0, 12, 1), (100, 12, 1)])
def test_exact_probe_unlocks_without_counting_already_feasible_records(store, world, budget, time, expected):
    constraints = {**world["defaults"], "budget_cents": 700, "time_budget_minutes": 20}
    add_session(store, world, constraints=constraints)
    cohort = one_cohort(audit_choices(store, budget_delta_cents=budget, time_delta_minutes=time))
    assert cohort["counts"]["no_plan_fits"] == 1
    assert cohort["counts"]["newly_feasible_under_probe"] == expected
    assert cohort["denominators"]["baseline_no_feasible_initial_choices"] == 1
    assert cohort["probe_constraints"]["travel_mode"] == "walk"
    assert cohort["probe_constraints"]["max_stores"] == 1


def test_wallet_cap_and_unknown_wallet_are_not_unlocked_by_budget(store, world):
    zero = with_wallet(world, reserve=22000)
    unknown = with_wallet(world, broken=True)
    add_session(store, zero)
    add_session(store, unknown)
    report = audit_choices(store, budget_delta_cents=10000, time_delta_minutes=120)
    assert len(report["cohorts"]) == 2
    ready = next(c for c in report["cohorts"] if c["financial_context"]["status"] == "ready")
    review = next(c for c in report["cohorts"] if c["financial_context"]["status"] == "needs_review")
    assert ready["financial_context"]["spendable_now_cents"] == 0
    assert ready["counts"]["no_plan_fits"] == 1
    assert ready["counts"]["newly_feasible_under_probe"] == 0
    assert review["counts"]["needs_review"] == 1
    assert review["denominators"]["probe_eligible_initial_choices"] == 0
    assert review["denominators"]["dominance_eligible_initial_choices"] == 0
    serialized = json.dumps(report)
    assert "customer_id" not in serialized and "account_id" not in serialized and "fixture-steady-checking" not in serialized


def test_zero_cost_and_zero_duration_are_valid_without_false_dominance(store, world):
    free = with_wallet(world, reserve=22000)
    for shop in free["stores"]:
        shop["prices"]["milk"] = 0
        shop["longitude"] = 0
        shop["shopping_minutes"] = 0
    add_session(store, free)
    cohort = one_cohort(audit_choices(store))
    assert cohort["counts"]["selected_plan_fits"] == 1
    assert cohort["counts"]["modeled_dominated"] == 0
    assert cohort["financial_context"]["spendable_now_cents"] == 0


def test_versions_modes_wallets_and_demo_records_are_separate(store, world):
    add_session(store, world)
    add_session(store, world, is_demo=True)
    different_version = copy.deepcopy(world)
    different_version["version"] = "v2"
    different_version["world_version"] = "prices-v2"
    add_session(store, different_version)
    add_session(store, with_wallet(world))
    add_session(store, world, constraints={**world["defaults"], "travel_mode": "drive"})
    report = audit_choices(store, data_mode="all")
    assert len(report["cohorts"]) == 5
    assert len({c["id"] for c in report["cohorts"]}) == 5
    assert sum(c["is_demo"] for c in report["cohorts"]) == 1
    assert {c["scenario_version"] for c in report["cohorts"]} == {"v1", "v2"}
    assert len(audit_choices(store)["cohorts"]) == 4
    assert one_cohort(audit_choices(store, data_mode="demo"))["is_demo"]
    assert audit_choices(store, scenario_id="missing")["total_sessions"] == 0
    assert report["analyzed_sessions"] == 5


def test_mixed_travel_modes_are_not_changed_by_analyst_probe(store, world):
    walk = {**world["defaults"], "budget_cents": 700, "time_budget_minutes": 20}
    drive = {**walk, "travel_mode": "drive"}
    add_session(store, world, constraints=walk, selected="far")
    add_session(store, world, constraints=drive, selected="far")
    report = audit_choices(store)
    by_mode = {c["initial_constraints"]["travel_mode"]: c for c in report["cohorts"]}
    assert by_mode["walk"]["counts"]["no_plan_fits"] == 1
    assert by_mode["drive"]["counts"]["selected_plan_fits"] == 1
    assert by_mode["walk"]["counts"]["newly_feasible_under_probe"] == 0


def test_revised_duplicate_and_missing_choices_do_not_inflate_observations(store, world):
    session = add_session(store, world, initial=False)
    identity = str(uuid4())
    add_choice(store, session, world, world["defaults"], "near", identity=identity)
    add_choice(store, session, world, world["defaults"], "near", identity=identity)
    for _ in range(4):
        add_choice(store, session, world, world["defaults"], "far", stage="revised")
    add_session(store, world, initial=False)
    report = audit_choices(store)
    assert report["selected_sessions"] == 2 and report["analyzed_sessions"] == 1
    assert report["skipped_no_initial_sessions"] == 1
    assert one_cohort(report)["counts"]["modeled_dominated"] == 1


def test_cached_arithmetic_is_recomputed_and_invalid_initial_is_reported(store, world):
    session = add_session(store, world)
    invalid = add_session(store, world)
    with store.connect() as connection:
        row = connection.execute("SELECT evaluation FROM choices WHERE session_id=?", (session["id"],)).fetchone()
        value = json.loads(row["evaluation"])
        value.update(total_cost_cents=999999999, feasible=False, total_minutes=999999)
        connection.execute("UPDATE choices SET evaluation=? WHERE session_id=?", (json.dumps(value), session["id"]))
        connection.execute("UPDATE choices SET evaluation=? WHERE session_id=?", (json.dumps({"constraints": world["defaults"]}), invalid["id"]))
    report = audit_choices(store)
    assert report["analyzed_sessions"] == 1 and report["skipped_invalid_initial_sessions"] == 1
    assert one_cohort(report)["counts"]["selected_plan_fits"] == 1


def test_selection_limit_and_stable_order_count_incomplete_sessions(store, world):
    sessions = [add_session(store, world, initial=False) for _ in range(201)]
    by_id = sorted(sessions, key=lambda s: s["id"])
    with store.connect() as connection:
        connection.execute("UPDATE sessions SET created_at='2026-01-01T00:00:00Z'")
    add_choice(store, by_id[0], world, world["defaults"], "near")
    add_choice(store, by_id[-1], world, world["defaults"], "near")
    report = audit_choices(store)
    assert report["total_sessions"] == 201 and report["selected_sessions"] == 200
    assert report["truncated"] and report["max_sessions"] == 200
    assert report["analyzed_sessions"] == 1 and report["skipped_no_initial_sessions"] == 199
    assert one_cohort(report)["example_ids"]["selected_plan_fits"] == [by_id[0]["id"]]


def test_examples_stay_in_cohort_and_cap_at_three(store, world):
    first = {add_session(store, world)["id"] for _ in range(5)}
    changed = copy.deepcopy(world)
    changed["version"] = "v2"
    second = {add_session(store, changed)["id"] for _ in range(4)}
    for cohort in audit_choices(store)["cohorts"]:
        ids = first if cohort["scenario_version"] == "v1" else second
        assert len(cohort["example_ids"]["selected_plan_fits"]) == 3
        assert set(cohort["example_ids"]["selected_plan_fits"]) <= ids
        assert set(cohort["example_ids"]["modeled_dominated"]) <= ids


def test_probe_application_bounds_are_disclosed_and_source_is_unchanged(store, world):
    constraints = {**world["defaults"], "budget_cents": 1_000_000, "time_budget_minutes": 1440}
    session = add_session(store, world, constraints=constraints)
    before = store.session(session["id"], session["token"])
    report = audit_choices(store, budget_delta_cents=10000, time_delta_minutes=120)
    cohort = one_cohort(report)
    assert cohort["probe_clipped_by_app_bounds"]
    assert cohort["probe_constraints"] == constraints
    assert report["probe"]["combined"]
    assert store.session(session["id"], session["token"]) == before


def test_registrar_requires_analyst_auth_and_validates_only_fixed_queries(store, world):
    add_session(store, world)
    app = FastAPI()

    def require_admin(authorization: str | None = Header(default=None)):
        if authorization != "Bearer analyst":
            raise HTTPException(401, "Analyst token required")

    register_audit_routes(app, store, require_admin)
    with TestClient(app) as client:
        assert client.get("/api/v1/analytics/audit").status_code == 401
        auth = {"Authorization": "Bearer analyst"}
        result = client.get("/api/v1/analytics/audit?budget_delta_cents=100&time_delta_minutes=5", headers=auth)
        assert result.status_code == 200
        assert result.json()["probe"]["combined"]
        for query in ["budget_delta_cents=-1", "budget_delta_cents=10001", "time_delta_minutes=121", "data_mode=invalid"]:
            assert client.get("/api/v1/analytics/audit?" + query, headers=auth).status_code == 422
        injection = client.get("/api/v1/analytics/audit", params={"scenario_id": "' OR 1=1 --"}, headers=auth)
        assert injection.status_code == 200 and injection.json()["total_sessions"] == 0
