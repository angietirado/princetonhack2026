"""Location comparison changes only home and cannot weaken session resources."""

import copy
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from spending_api.lab_engine import evaluate
from spending_api.lab_location import compare_locations, register_location_routes
from spending_api.lab_store import LabConflict, LabNotFound, LabStore, LabUnauthorized
from spending_api.lab_wallet import make_fixture_wallets


@pytest.fixture
def world():
    return {
        "id": "home-a", "version": "scenario-v1", "name": "Home A", "description": "Fixed-world comparison",
        "world_id": "controlled", "world_version": "world-v1",
        "home": {"label": "Home A", "latitude": 0, "longitude": 0, "source": "costar",
                 "source_property_id": "property-a", "source_record_id": "record-a", "unit_number": "1",
                 "recorded_address": "1 Example Street", "housing_price_basis": "monthly_rent",
                 "source_monetary_unit": 1, "monthly_rent_cents": 200000, "bedrooms_min": 1,
                 "living_area_sqft_min": 700, "source_listing_date": "2026-11-04T00:00:00Z"},
        "defaults": {"budget_cents": 1200, "time_budget_minutes": 30, "travel_mode": "walk", "max_stores": 1},
        "basket": [{"id": "milk", "name": "Milk", "quantity": 2, "unit": "gallon", "category": "Dairy"}],
        "stores": [
            {"id": "a-store", "name": "A store", "latitude": 0, "longitude": 0.001,
             "shopping_minutes": 5, "prices": {"milk": 500}},
            {"id": "b-store", "name": "B store", "latitude": 0, "longitude": 0.02,
             "shopping_minutes": 5, "prices": {"milk": 300}},
        ],
        "travel": {"method": "modeled_distance", "road_factor": 1, "walk_kmh": 5,
                   "drive_kmh": 30, "drive_cost_cents_per_km": 25, "drive_parking_minutes_per_stop": 3},
        "provenance": {"prices": "Simulated", "travel": "Modeled"},
    }


def other_home(world, identity="b", longitude=0.019):
    candidate = copy.deepcopy(world)
    candidate.update(id="home-" + identity, name="Home " + identity.upper())
    candidate["home"].update(label="Home " + identity.upper(), longitude=longitude,
                             source_property_id="property-" + identity, source_record_id="record-" + identity,
                             recorded_address=identity + " Example Street", monthly_rent_cents=230000,
                             bedrooms_min=2, living_area_sqft_min=1200)
    return candidate


def attach_wallet(world, *, reserve=21400, broken=False):
    result = copy.deepcopy(world)
    snapshot = make_fixture_wallets()[0]
    if broken:
        snapshot["available_cents"] += 1
    result["financial_context"] = {"profile_name": "Controlled", "snapshot": snapshot,
                                   "projection_inputs": {"days": 7, "reserve_cents": reserve,
                                                         "expected_income": [], "events": []}}
    return result


def report(world, candidates, **kwargs):
    return compare_locations(world, kwargs.get("constraints", world["defaults"]), {"milk": "a-store"}, candidates)


def test_same_assignment_and_optimized_comparisons_are_separate_and_immutable(world):
    candidate = other_home(world)
    before = copy.deepcopy((world, candidate))
    result = report(world, [world, candidate])
    assert result["status"] == "ok" and len(result["locations"]) == 1
    baseline, moved = result["baseline"], result["locations"][0]
    assert baseline["is_current"] and baseline["home"] == world["home"]
    assert not moved["is_current"] and moved["home"] == candidate["home"]
    assert moved["same_assignment"]["evaluation"]["assignments"] == {"milk": "a-store"}
    assert moved["same_assignment"]["delta"]["basket_cost_cents"] == 0
    assert moved["same_assignment"]["delta"]["total_cost_cents"] == 0  # Walking transport stays free.
    assert moved["same_assignment"]["delta"]["total_minutes"] > 0
    assert not moved["same_assignment"]["evaluation"]["feasible"]
    assert moved["optimized"]["plans"][0]["assignments"] == {"milk": "b-store"}
    assert moved["optimized"]["plans"][0]["total_cost_cents"] == 600
    assert moved["rent"]["delta_monthly_cents"] == 30000
    assert moved["rent"]["fees_monthly_cents"] is None and moved["rent"]["all_in_monthly_cents"] is None
    assert world["home"]["bedrooms_min"] != moved["home"]["bedrooms_min"]
    assert all(value == 0 for value in baseline["same_assignment"]["delta"].values())
    assert (world, candidate) == before


def test_driving_cost_delta_comes_only_from_transport(world):
    constraints = {**world["defaults"], "travel_mode": "drive"}
    result = report(world, [other_home(world)], constraints=constraints)
    moved = result["locations"][0]
    assert moved["same_assignment"]["delta"]["basket_cost_cents"] == 0
    assert moved["same_assignment"]["delta"]["transport_cost_cents"] > 0
    assert moved["same_assignment"]["delta"]["total_cost_cents"] == moved["same_assignment"]["delta"]["transport_cost_cents"]
    assert moved["same_assignment"]["evaluation"]["constraints"] == constraints
    assert all(p["constraints"] == constraints for p in moved["optimized"]["plans"])


@pytest.mark.parametrize("tamper", ["price", "quantity", "store_location", "travel", "world_id", "world_version"])
def test_declared_version_cannot_hide_world_tampering(world, tamper):
    bad = other_home(world)
    if tamper == "price":
        bad["stores"][0]["prices"]["milk"] = 1
    elif tamper == "quantity":
        bad["basket"][0]["quantity"] = 1
    elif tamper == "store_location":
        bad["stores"][0]["longitude"] += 1
    elif tamper == "travel":
        bad["travel"]["road_factor"] = 2
    else:
        bad[tamper] = "different"
    result = report(world, [bad])
    assert result["status"] == "no_compatible_homes" and not result["locations"]
    reason = "world_mismatch" if tamper.startswith("world_") else "content_mismatch"
    assert result["catalog_summary"]["skipped"][reason] == 1


def test_legacy_and_old_frozen_worlds_do_not_fall_forward_to_current_catalog(world):
    catalog = [other_home(world)]
    legacy = copy.deepcopy(world)
    del legacy["world_version"]
    result = report(legacy, catalog)
    assert result["status"] == "unsupported_world" and not result["locations"]
    assert result["baseline"]["home"] == legacy["home"]
    older = copy.deepcopy(world)
    older["world_version"] = "old-world"
    assert report(older, catalog)["status"] == "no_compatible_homes"


def test_refreshed_current_record_and_reused_scenario_id_do_not_become_moves(world):
    refreshed = copy.deepcopy(world)
    refreshed["id"] = "new-catalog-id"
    refreshed["home"].update(source_record_id="new-listing-record", monthly_rent_cents=250000,
                              source_listing_date="2026-11-13T00:00:00Z", label="Refreshed listing")
    reused = other_home(world)
    reused["id"] = world["id"]
    result = report(world, [refreshed, reused])
    assert result["status"] == "no_compatible_homes"
    assert result["catalog_summary"]["skipped"]["current_home"] == 2
    assert result["baseline"]["home"] == world["home"]
    assert result["baseline"]["rent"]["recorded_monthly_cents"] == 200000
    # A known distinct unit is a different home, even in the same building.
    distinct_unit = copy.deepcopy(refreshed)
    distinct_unit["home"]["unit_number"] = "2"
    assert len(report(world, [distinct_unit])["locations"]) == 1


def test_deduplication_and_three_home_limit_are_explicit(world):
    candidates = [other_home(world, str(i), 0.01 * i) for i in range(1, 6)]
    duplicate = copy.deepcopy(candidates[0])
    duplicate["id"] = "duplicate-id"
    duplicate["home"]["source_record_id"] = "refreshed-record"
    candidates.append(duplicate)
    result = report(world, candidates)
    assert len(result["locations"]) == 3
    assert result["catalog_summary"]["compatible_distinct_home_count"] == 5
    assert result["catalog_summary"]["truncated"]
    assert result["catalog_summary"]["skipped"]["duplicate_home"] == 1
    assert result == report(world, list(reversed(candidates)))


def test_candidate_wallet_and_defaults_are_ignored_while_frozen_cap_is_preserved(world):
    baseline = attach_wallet(world)
    candidate = attach_wallet(other_home(world), reserve=0)
    candidate["defaults"].update(budget_cents=999999, time_budget_minutes=1440, travel_mode="drive", max_stores=2)
    result = report(baseline, [candidate])
    assert result["wallet_summary"]["spendable_now_cents"] == 600
    assert result["wallet_summary"]["reserve_cents"] == 21400
    assert result["baseline"]["optimized"]["status"] == "no_feasible_plan"
    moved = result["locations"][0]
    assert moved["optimized"]["status"] == "ok"
    assert all(p["total_cost_cents"] <= 600 for p in moved["optimized"]["plans"])
    assert all(p["constraints"] == baseline["defaults"] for p in moved["optimized"]["plans"])
    assert not moved["same_assignment"]["evaluation"]["feasible"]


def test_unknown_wallet_is_explicit_and_cannot_offer_feasible_plans(world):
    baseline = attach_wallet(world, broken=True)
    result = report(baseline, [other_home(world)])
    assert result["status"] == "needs_review"
    assert result["wallet_summary"]["spendable_now_cents"] is None
    for place in [result["baseline"], *result["locations"]]:
        assert place["optimized"]["status"] == "needs_review" and not place["optimized"]["plans"]
        assert not place["same_assignment"]["evaluation"]["feasible"]


@pytest.mark.parametrize("change", [
    {"monthly_rent_cents": None}, {"housing_price_basis": "sale_price"},
    {"source_monetary_unit": 2}, {"source_monetary_unit": None},
    {"source_monetary_unit": True}, {"monthly_rent_cents": "230000"}, {"housing_price_basis": None},
])
def test_untrusted_or_missing_monthly_usd_rent_stays_unknown(world, change):
    candidate = other_home(world)
    candidate["home"].update(change)
    result = report(world, [candidate])
    rent = result["locations"][0]["rent"]
    assert rent["recorded_monthly_cents"] is None and rent["delta_monthly_cents"] is None
    assert rent["currency"] is None


def test_missing_baseline_rent_does_not_create_a_zero_baseline(world):
    candidate = other_home(world)
    del world["home"]["monthly_rent_cents"]
    result = report(world, [candidate])
    assert result["baseline"]["rent"]["recorded_monthly_cents"] is None
    assert result["locations"][0]["rent"]["recorded_monthly_cents"] == 230000
    assert result["locations"][0]["rent"]["delta_monthly_cents"] is None


def test_malformed_candidate_is_skipped_and_invalid_assignments_rejected(world):
    candidate = other_home(world)
    invalid = copy.deepcopy(candidate)
    invalid["stores"][0]["prices"] = {}
    result = report(world, [invalid, candidate, "not a scenario"])
    assert len(result["locations"]) == 1
    assert result["catalog_summary"]["skipped"]["invalid"] == 2
    with pytest.raises(ValueError):
        compare_locations(world, world["defaults"], {}, [candidate])
    with pytest.raises(ValueError):
        compare_locations(world, {**world["defaults"], "budget_cents": True}, {"milk": "a-store"}, [candidate])


@pytest.fixture
def api(tmp_path, world):
    store = LabStore(tmp_path / "locations.sqlite3")
    app = FastAPI()
    for exception, status in ((LabConflict, 409), (LabUnauthorized, 401), (LabNotFound, 404)):
        async def handle(_request, error, status=status):
            return JSONResponse({"detail": str(error)}, status_code=status)
        app.add_exception_handler(exception, handle)
    candidates = {world["id"]: world, "home-b": other_home(world)}
    register_location_routes(app, store, candidates)
    return app, store


def saved_session(store, world, *, initial=True):
    session = store.create_session({"request_id": str(uuid4()), "constraints": world["defaults"], "is_demo": False}, world)
    if initial:
        save(store, session, world)
    return session, {"Authorization": "Bearer " + session["token"]}, f"/api/v1/sessions/{session['id']}/locations"


def save(store, session, world, *, stage="initial", constraints=None, selected="a-store"):
    return store.add_choice(session["id"], {"request_id": str(uuid4()), "stage": stage},
                            evaluate(world, {"milk": selected}, constraints or world["defaults"]))


def test_api_session_auth_initial_gate_defaults_and_no_saved_mutation(api, world):
    app, store = api
    session, auth, path = saved_session(store, world, initial=False)
    with TestClient(app) as client:
        assert client.post(path, json={}).status_code == 401
        assert client.post(path, headers=auth, json={}).status_code == 409
        _, other_auth, _ = saved_session(store, world)
        assert client.post(path, headers=other_auth, json={}).status_code == 401
        save(store, session, world)
        latest_constraints = {**world["defaults"], "time_budget_minutes": 45}
        latest = save(store, session, world, stage="revised", constraints=latest_constraints, selected="b-store")
        before = store.session(session["id"], session["token"])
        result = client.post(path, headers=auth, json={})
        assert result.status_code == 200
        report_value = result.json()
        assert report_value["anchor_choice_id"] == latest["id"] and not report_value["stale"]
        assert report_value["constraints"] == latest_constraints
        assert report_value["assignments"] == {"milk": "b-store"}
        assert store.session(session["id"], session["token"]) == before
        explicit = client.post(path, headers=auth, json={"constraints": world["defaults"], "assignments": {"milk": "a-store"}})
        assert explicit.status_code == 200 and explicit.json()["constraints"] == world["defaults"]


@pytest.mark.parametrize("body", [{"assignments": {}}, {"assignments": {"milk": "invented"}},
                                    {"wallet_summary": {"spendable_now_cents": 999999}},
                                    {"constraints": {"budget_cents": 1}}])
def test_api_rejects_malformed_input_and_client_wallets(api, world, body):
    app, store = api
    _, auth, path = saved_session(store, world)
    with TestClient(app) as client:
        assert client.post(path, headers=auth, json=body).status_code == 422


def test_new_choice_during_comparison_marks_read_only_report_stale(api, world, monkeypatch):
    app, store = api
    session, auth, path = saved_session(store, world)
    import spending_api.lab_location as module
    original = module.compare_locations

    def concurrent_choice(*args):
        result = original(*args)
        save(store, session, world, stage="revised", selected="b-store")
        return result

    monkeypatch.setattr(module, "compare_locations", concurrent_choice)
    with TestClient(app) as client:
        result = client.post(path, headers=auth, json={})
        assert result.status_code == 200 and result.json()["stale"]
        latest = store.session(session["id"], session["token"])["choices"][-1]
        assert result.json()["anchor_choice_id"] != latest["id"]
