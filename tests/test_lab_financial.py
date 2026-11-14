"""Optional frozen wallets constrain all shopping surfaces without replacing user limits."""

import copy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from spending_api.lab_api import create_app
from spending_api.lab_financial import (
    attach_wallet, create_financial_catalog, financial_evaluate, financial_recommend, financial_summary,
)
from spending_api.lab_wallet import freeze_wallet


@pytest.fixture
def scenario():
    return {
        "id": "financial-test", "version": "1", "name": "Financial test", "description": "A controlled basket",
        "home": {"label": "Home", "latitude": 0, "longitude": 0},
        "defaults": {
            "budget_cents": 10000, "time_budget_minutes": 100.0, "travel_mode": "walk", "max_stores": 2,
        },
        "basket": [{"id": "basket", "name": "Required basket", "quantity": 1, "unit": "basket", "category": "Food"}],
        "stores": [
            {"id": "near", "name": "Near shop", "latitude": 0, "longitude": 0.001,
             "shopping_minutes": 5, "prices": {"basket": 4000}},
            {"id": "far", "name": "Far shop", "latitude": 0, "longitude": 0.01,
             "shopping_minutes": 5, "prices": {"basket": 2000}},
        ],
        "travel": {
            "method": "modeled_distance", "road_factor": 1.1, "walk_kmh": 5,
            "drive_kmh": 30, "drive_cost_cents_per_km": 25, "drive_parking_minutes_per_stop": 3,
        },
        "provenance": {"prices": "Simulated"},
    }


@pytest.fixture
def app(tmp_path, scenario, monkeypatch):
    import json
    monkeypatch.setenv("LAB_AI_ENABLED", "false")
    source = tmp_path / "scenarios.json"
    source.write_text(json.dumps({"schema_version": 1, "scenarios": [scenario]}), encoding="utf-8")
    return create_app(scenarios_path=source, db_path=tmp_path / "wallet-api.sqlite3", admin_token="")


def request(scenario, profile="tight"):
    body = {
        "request_id": str(uuid4()), "scenario_id": scenario["id"],
        "constraints": scenario["defaults"], "is_demo": False, "consent": True,
    }
    if profile is not None:
        body["wallet_profile_id"] = profile
    return body


def start(client, scenario, profile="tight"):
    body = request(scenario, profile)
    response = client.post("/api/v1/sessions", json=body)
    assert response.status_code == 201, response.text
    session = response.json()
    return session, {"Authorization": "Bearer " + session["token"]}, body


def choose(client, session, auth, store="near", stage="initial"):
    response = client.post(
        f"/api/v1/sessions/{session['id']}/choices", headers=auth,
        json={"request_id": str(uuid4()), "stage": stage, "assignments": {"basket": store}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_public_catalog_is_fictional_cash_context_and_contains_no_keys(app):
    with TestClient(app) as client:
        result = client.get("/api/v1/wallets").json()
        assert [item["id"] for item in result["wallets"]] == ["steady", "tight", "buffer"]
        assert [item["available_cents"] for item in result["wallets"]] == [40000, 14000, 60000]
        assert [item["spendable_now_cents"] for item in result["wallets"]] == [22000, 3000, 36000]
        assert all(item["source"] == "fixture" for item in result["wallets"])
        assert "not live Nessie" in result["notice"]
        assert "api_key" not in str(result).lower()


def test_optional_profile_leaves_old_session_shape_and_behavior(app, scenario):
    with TestClient(app) as client:
        session, auth, _ = start(client, scenario, None)
        assert "financial_context" not in session["scenario"]
        initial = choose(client, session, auth)
        assert initial["evaluation"]["feasible"] is True
        assert "wallet" not in initial["evaluation"]
        wallet = client.get(f"/api/v1/sessions/{session['id']}/wallet", headers=auth).json()
        assert wallet["summary"] is None and wallet["profile"] is None


def test_legacy_create_retry_preserves_token_and_hash(app, scenario):
    body = request(scenario, None)
    # Exact canonical shape used before wallet_profile_id existed.
    original = app.state.lab_store.create_session(body, app.state.lab_scenarios[scenario["id"]])
    with TestClient(app) as client:
        retry = client.post("/api/v1/sessions", json=body)
        assert retry.status_code == 201
        assert retry.json() == original
        explicit_null = client.post("/api/v1/sessions", json={**body, "wallet_profile_id": None})
        assert explicit_null.json() == original
        conflict = client.post("/api/v1/sessions", json={**body, "wallet_profile_id": "tight"})
        assert conflict.status_code == 409


def test_selection_is_frozen_and_wallet_endpoint_requires_session_token(app, scenario):
    with TestClient(app) as client:
        session, auth, body = start(client, scenario)
        context = session["scenario"]["financial_context"]
        assert context["profile_id"] == "tight"
        assert context["snapshot"]["source"] == "fixture"
        assert context["projection"]["spendable_now_cents"] == 3000
        path = f"/api/v1/sessions/{session['id']}/wallet"
        assert client.get(path).status_code == 401
        before = client.get(path, headers=auth).json()
        assert before["summary"]["spendable_now_cents"] == 3000
        app.state.lab_wallet_profiles["tight"]["projection_inputs"]["reserve_cents"] = 3000
        assert client.get(path, headers=auth).json() == before
        assert client.post("/api/v1/sessions", json=body).json() == session


@pytest.mark.parametrize("extra", [
    {"wallet_profile_id": "live-nessie"},
    {"wallet_profile_id": {"cash": 999999}},
    {"financial_context": {"snapshot": {"available_cents": 999999}}},
    {"wallet_cash_cents": 999999},
])
def test_clients_cannot_submit_bank_context_or_arbitrary_cash(app, scenario, extra):
    with TestClient(app) as client:
        assert client.post("/api/v1/sessions", json={**request(scenario), **extra}).status_code == 422


def test_evaluate_and_save_preserve_user_budget_but_record_wallet_infeasibility(app, scenario):
    with TestClient(app) as client:
        session, auth, _ = start(client, scenario)
        response = client.post(
            f"/api/v1/sessions/{session['id']}/evaluate", headers=auth,
            json={"assignments": {"basket": "near"}},
        )
        result = response.json()
        assert result["constraints"]["budget_cents"] == 10000
        assert result["total_cost_cents"] == 4000
        assert result["feasible"] is False
        assert any("wallet" in item.lower() for item in result["violations"])
        assert result["wallet"]["summary"]["spendable_now_cents"] == 3000
        assert result["wallet"]["after_choice"]["ending_available_cents"] == -1000
        assert result["wallet"]["after_choice"]["shortfall_cents"] == 1000
        initial = choose(client, session, auth)
        assert initial["evaluation"]["feasible"] is False


def test_wallet_recommendations_do_not_rewrite_user_constraints(app, scenario):
    with TestClient(app) as client:
        session, auth, _ = start(client, scenario)
        choose(client, session, auth)
        result = client.post(
            f"/api/v1/sessions/{session['id']}/recommendations", headers=auth, json={},
        ).json()
        assert result["status"] == "ok"
        assert result["effective_spending_cap_cents"] == 3000
        assert result["plans"]
        assert all(plan["total_cost_cents"] <= 3000 for plan in result["plans"])
        assert all(plan["constraints"]["budget_cents"] == 10000 for plan in result["plans"])
        assert all(plan["feasible"] for plan in result["plans"])


def test_revisions_and_repeated_previews_do_not_accumulate_spending(app, scenario):
    with TestClient(app) as client:
        session, auth, _ = start(client, scenario)
        choose(client, session, auth)
        first = choose(client, session, auth, "far", "revised")
        second = choose(client, session, auth, "far", "revised")
        for choice in (first, second):
            assert choice["evaluation"]["wallet"]["after_choice"]["local_spending_cents"] == 2000
            assert choice["evaluation"]["wallet"]["after_choice"]["ending_available_cents"] == 1000
        wallet = client.get(f"/api/v1/sessions/{session['id']}/wallet", headers=auth).json()
        assert wallet["summary"]["spendable_now_cents"] == 3000
        assert wallet["summary"]["local_spending_cents"] == 0


def test_future_income_does_not_fund_a_trip_now(app, scenario):
    app.state.lab_wallet_profiles["tight"]["projection_inputs"]["expected_income"] = [{
        "id": "pay", "label": "Explicit example pay", "amount_cents": 5000,
        "available_at": "2026-11-18T00:00:00Z",
    }]
    with TestClient(app) as client:
        session, auth, _ = start(client, scenario)
        result = choose(client, session, auth)["evaluation"]
        assert result["wallet"]["summary"]["shopping_allowance_cents"] == 8000
        assert result["wallet"]["summary"]["spendable_now_cents"] == 3000
        assert result["feasible"] is False


def test_reserve_is_applied_once(scenario):
    catalog = create_financial_catalog()
    catalog["tight"]["projection_inputs"]["reserve_cents"] = 1000
    scenario = attach_wallet(scenario, "tight", catalog)
    result = financial_evaluate(scenario, {"basket": "far"}, scenario["defaults"])
    assert result["wallet"]["summary"]["spendable_now_cents"] == 2000
    assert result["feasible"] is True
    assert result["wallet"]["after_choice"]["spendable_now_cents"] == 0


def test_unavailable_travel_mode_is_never_silently_changed(app, scenario):
    app.state.lab_wallet_profiles["tight"]["projection_inputs"]["events"] = [{"kind": "car_unavailable"}]
    with TestClient(app) as client:
        session, auth, _ = start(client, scenario)
        choose(client, session, auth, "far")
        constraints = {**scenario["defaults"], "travel_mode": "drive"}
        evaluation = client.post(
            f"/api/v1/sessions/{session['id']}/evaluate", headers=auth,
            json={"assignments": {"basket": "far"}, "constraints": constraints},
        ).json()
        assert evaluation["feasible"] is False
        assert evaluation["constraints"]["travel_mode"] == "drive"
        recommendations = client.post(
            f"/api/v1/sessions/{session['id']}/recommendations", headers=auth,
            json={"constraints": constraints},
        ).json()
        assert recommendations["plans"] == []
        assert recommendations["wallet_summary"]["allowed_travel_modes"] == ["walk"]


def test_unknown_allowance_blocks_recommendations_and_chat_claims(app, scenario):
    context = app.state.lab_wallet_profiles["tight"]
    changed = copy.deepcopy(context["snapshot"])
    changed["obligations"][0]["due_at"] = None
    context["snapshot"] = freeze_wallet(changed)
    with TestClient(app) as client:
        session, auth, _ = start(client, scenario)
        assert choose(client, session, auth, "far")["evaluation"]["feasible"] is False
        response = client.post(
            f"/api/v1/sessions/{session['id']}/recommendations", headers=auth, json={},
        ).json()
        assert response["status"] == "needs_review"
        assert response["plans"] == []
        chat = client.post(
            f"/api/v1/sessions/{session['id']}/chat", headers=auth,
            json={"request_id": str(uuid4()), "message": "budget $1000"},
        ).json()
        assert chat["status"] == "clarification"
        assert chat["plans"] == []
        assert "needs review" in chat["reply"]
        assert chat["constraints"] == scenario["defaults"]


def test_chat_cannot_bypass_wallet_cap_with_larger_budget(app, scenario):
    with TestClient(app) as client:
        session, auth, _ = start(client, scenario)
        choose(client, session, auth)
        chat = client.post(
            f"/api/v1/sessions/{session['id']}/chat", headers=auth,
            json={"request_id": str(uuid4()), "message": "budget $1000"},
        ).json()
        assert chat["status"] == "ok"
        assert chat["constraints"]["budget_cents"] == 100000
        assert chat["plans"] and all(plan["total_cost_cents"] <= 3000 for plan in chat["plans"])
        assert chat["wallet_summary"]["spendable_now_cents"] == 3000
        assert "$30.00 available for shopping now" in chat["reply"]


def test_corrupt_snapshot_cannot_be_overridden_by_cached_projection(scenario):
    scenario = attach_wallet(scenario, "tight", create_financial_catalog())
    scenario["financial_context"]["snapshot"]["available_cents"] = 999999
    scenario["financial_context"]["projection"]["spendable_now_cents"] = 999999
    summary = financial_summary(scenario)
    assert summary["status"] == "needs_review"
    assert summary["spendable_now_cents"] is None
    assert financial_recommend(scenario, scenario["defaults"])["plans"] == []


def test_zero_cash_can_fund_only_a_truly_free_complete_basket(scenario):
    catalog = create_financial_catalog()
    catalog["tight"]["projection_inputs"]["reserve_cents"] = 3000
    scenario = attach_wallet(scenario, "tight", catalog)
    for store in scenario["stores"]:
        store["prices"]["basket"] = 0
    result = financial_recommend(scenario, scenario["defaults"])
    assert result["status"] == "ok"
    assert result["effective_spending_cap_cents"] == 0
    assert result["plans"] and all(plan["total_cost_cents"] == 0 for plan in result["plans"])
    assert result["feasible_plan_count"] > 0
    for store in scenario["stores"]:
        store["prices"]["basket"] = 1
    result = financial_recommend(scenario, scenario["defaults"])
    assert result["status"] == "no_feasible_plan"
    assert result["plans"] == []
    assert result["feasible_plan_count"] == 0



@pytest.mark.parametrize("cap", [-1, True, 1.5, "30"])
def test_optimizer_additional_cap_requires_nonnegative_integer_cents(scenario, cap):
    from spending_api.lab_engine import recommend
    with pytest.raises(ValueError):
        recommend(scenario, scenario["defaults"], cost_cap_cents=cap)

