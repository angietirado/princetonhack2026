"""Session isolation, durable retries, immutable facts, and bounded model work."""

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langsmith.run_helpers import get_tracing_context

from spending_api.lab_api import create_app
from spending_api.lab_investigation_api import InvestigationRequest
from spending_api.lab_wallet import make_fixture_wallets


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    monkeypatch.setenv("LAB_AI_ENABLED", "false")


@pytest.fixture
def files(tmp_path):
    scenario = {
        "id": "investigate", "version": "v1", "name": "Investigation example", "description": "A controlled test",
        "home": {"label": "Home", "latitude": 0, "longitude": 0},
        "defaults": {"budget_cents": 800, "time_budget_minutes": 20, "travel_mode": "walk", "max_stores": 1},
        "basket": [{"id": "milk", "name": "Milk", "quantity": 2, "unit": "gallon", "category": "Dairy"}],
        "stores": [
            {"id": "near", "name": "Near", "latitude": 0, "longitude": 0.001,
             "shopping_minutes": 5, "prices": {"milk": 500}},
            {"id": "far", "name": "Far", "latitude": 0, "longitude": 0.01,
             "shopping_minutes": 5, "prices": {"milk": 300}},
        ],
        "travel": {"method": "modeled_distance", "road_factor": 1, "walk_kmh": 5,
                   "drive_kmh": 30, "drive_cost_cents_per_km": 25, "drive_parking_minutes_per_stop": 3},
        "provenance": {"prices": "Simulated", "travel": "Modeled"},
    }
    source, db = tmp_path / "scenarios.json", tmp_path / "lab.sqlite3"
    write_scenario(source, scenario)
    return source, db, scenario


def write_scenario(source, scenario):
    source.write_text(json.dumps({"schema_version": 1, "scenarios": [scenario]}), encoding="utf-8")


def start(client, scenario, initial=True):
    response = client.post("/api/v1/sessions", json={
        "request_id": str(uuid4()), "scenario_id": scenario["id"], "constraints": scenario["defaults"],
        "consent": True, "is_demo": False,
    })
    assert response.status_code == 201, response.text
    session = response.json()
    auth = {"Authorization": "Bearer " + session["token"]}
    if initial:
        choice(client, session, auth, "initial")
    return session, auth, f"/api/v1/sessions/{session['id']}/investigate"


def choice(client, session, auth, stage, *, constraints=None, store="near"):
    body = {"request_id": str(uuid4()), "stage": stage, "assignments": {"milk": store}}
    if constraints is not None:
        body["constraints"] = constraints
    result = client.post(f"/api/v1/sessions/{session['id']}/choices", headers=auth, json=body)
    assert result.status_code == 201, result.text
    return result.json()


def send(client, path, auth, **values):
    body = {"request_id": str(uuid4()), **values}
    response = client.post(path, headers=auth, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_auth_and_initial_choice_precede_investigation(files):
    source, db, scenario = files
    calls = []
    with TestClient(create_app(scenarios_path=source, db_path=db, investigation_interpreter=lambda p: calls.append(p))) as client:
        session, auth, path = start(client, scenario, initial=False)
        body = {"request_id": str(uuid4())}
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, headers=auth, json=body).status_code == 409
        _, other_auth, _ = start(client, scenario)
        assert client.post(path, headers=other_auth, json=body).status_code == 401
        assert calls == []
        assert client.get(f"/api/v1/sessions/{session['id']}", headers=auth).json()["choices"] == []


def test_offline_report_preserves_choices_and_frozen_scenario(files):
    source, db, scenario = files
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        session, auth, path = start(client, scenario)
        before = client.get(f"/api/v1/sessions/{session['id']}", headers=auth).json()
        result = send(client, path, auth, protected_constraints=["max_stores"], allowed_travel_modes=["walk"])
        assert result["mode"] == "deterministic" and result["status"] == "changes_available"
        assert result["minimum_requirements"]["minimum_budget_cents"] == 1000
        assert {p["kind"] for p in result["alternatives"]} == {"budget", "time"}
        assert result["anchor_choice_id"] == before["choices"][-1]["id"]
        assert result["stale"] is False
        after = client.get(f"/api/v1/sessions/{session['id']}", headers=auth).json()
        assert after == before
        assert result["constraints"] == scenario["defaults"]


def test_latest_choice_defaults_and_explicit_draft_settings(files):
    source, db, scenario = files
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        session, auth, path = start(client, scenario)
        revised = {**scenario["defaults"], "time_budget_minutes": 50}
        saved = choice(client, session, auth, "revised", constraints=revised, store="far")
        result = send(client, path, auth)
        assert result["constraints"] == revised
        assert result["selected_plan"]["assignments"] == {"milk": "far"}
        assert result["anchor_choice_id"] == saved["id"]
        explicit = send(client, path, auth, constraints=scenario["defaults"], assignments={"milk": "near"})
        assert explicit["constraints"] == scenario["defaults"]
        assert explicit["selected_plan"]["assignments"] == {"milk": "near"}
        assert len(client.get(f"/api/v1/sessions/{session['id']}", headers=auth).json()["choices"]) == 2


def test_retry_survives_restart_and_newer_choice_without_recomputation(files):
    source, db, scenario = files
    identity = str(uuid4())
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        session, auth, path = start(client, scenario)
        result = send(client, path, auth, request_id=identity)
        choice(client, session, auth, "revised", constraints={**scenario["defaults"], "budget_cents": 1400})
    calls = []
    with TestClient(create_app(scenarios_path=source, db_path=db, investigation_interpreter=lambda p: calls.append(p))) as client:
        assert send(client, path, auth, request_id=identity) == result
        assert calls == []
        changed = client.post(path, headers=auth, json={"request_id": identity, "message": "A different question"})
        assert changed.status_code == 409
        assert client.post(path, json={"request_id": identity}).status_code == 401


@pytest.mark.parametrize("extra", [
    {"wallet_summary": {"spendable_now_cents": 999999}},
    {"protected_constraints": ["rent"]}, {"protected_constraints": ["max_stores", "max_stores"]},
    {"allowed_travel_modes": ["fly"]}, {"allowed_travel_modes": ["walk", "walk"]},
    {"assignments": {}}, {"assignments": {"milk": "invented"}},
    {"message": " "}, {"message": "x" * 1501},
    {"constraints": {"budget_cents": 1_000_001, "time_budget_minutes": 10, "travel_mode": "walk", "max_stores": 1}},
])
def test_invalid_or_untrusted_request_fields_are_rejected(files, extra):
    source, db, scenario = files
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        _, auth, path = start(client, scenario)
        response = client.post(path, headers=auth, json={"request_id": str(uuid4()), **extra})
        assert response.status_code == 422
        assert "999999" not in response.text


def test_wallet_cash_is_derived_from_snapshot_not_cached_claims(files):
    source, db, scenario = files
    scenario["financial_context"] = {
        "profile_id": "steady", "profile_name": "Steady week", "snapshot": make_fixture_wallets()[0],
        "projection_inputs": {"days": 7, "reserve_cents": 21400, "expected_income": [],
                              "events": [{"kind": "car_unavailable"}]},
        "projection": {"status": "ready", "spendable_now_cents": 9999999},
    }
    write_scenario(source, scenario)
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        _, auth, path = start(client, scenario)
        report = send(client, path, auth, allowed_travel_modes=["walk", "drive"])
        assert report["wallet"]["spendable_now_cents"] == 600
        assert report["wallet"]["reserve_cents"] == 21400
        assert report["allowed_travel_modes"] == ["walk"]
        assert report["alternatives"] and all(p["plan"]["total_cost_cents"] <= 600 for p in report["alternatives"])
        assert all(p["kind"] != "budget" for p in report["alternatives"])


def test_invalid_financial_context_blocks_confident_affordability(files):
    source, db, scenario = files
    scenario["financial_context"] = {"snapshot": {"tampered": True}}
    write_scenario(source, scenario)
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        _, auth, path = start(client, scenario)
        report = send(client, path, auth)
        assert report["status"] == "needs_review"
        assert not report["alternatives"] and not report["feasible_plans"]


def test_callback_can_only_select_known_ids_and_tracing_is_disabled(files, monkeypatch):
    source, db, scenario = files
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    payloads = []

    def interpreter(payload):
        payloads.append(copy.deepcopy(payload))
        assert get_tracing_context()["enabled"] is False
        alternative = payload["report"]["alternatives"][0]
        # Even a callback mutating its input cannot replace canonical report facts.
        payload["report"]["constraints"]["budget_cents"] = 999999
        alternative["plan"]["total_cost_cents"] = 1
        return {"alternative_ids": [alternative["id"]], "evidence_ids": alternative["evidence_ids"],
                "question_id": "money_or_time"}

    with TestClient(create_app(scenarios_path=source, db_path=db, investigation_interpreter=interpreter)) as client:
        _, auth, path = start(client, scenario)
        result = send(client, path, auth)
        assert result["mode"] == "langchain"
        assert result["constraints"] == scenario["defaults"]
        assert result["alternatives"] == payloads[0]["report"]["alternatives"]
        assert len(payloads) == 1
        assert "token" not in json.dumps(payloads[0]) and "customer_id" not in json.dumps(payloads[0])


@pytest.mark.parametrize("selection", [
    {"alternative_ids": ["invented"]}, {"question_id": "invented"},
    {"evidence_ids": ["invented"]}, {"constraints": {"budget_cents": 999999}},
])
def test_invalid_callback_output_falls_back_without_fact_changes(files, selection):
    source, db, scenario = files
    with TestClient(create_app(scenarios_path=source, db_path=db, investigation_interpreter=lambda p: selection)) as client:
        _, auth, path = start(client, scenario)
        report = send(client, path, auth)
        assert report["mode"] == "fallback"
        assert report["constraints"] == scenario["defaults"]
        assert report["minimum_requirements"]["minimum_budget_cents"] == 1000


def test_timed_out_work_retains_slots_and_retries_do_not_call_again(files):
    source, db, scenario = files
    release = threading.Event()
    calls = []

    def slow(payload):
        calls.append(payload)
        release.wait(timeout=3)
        return {}

    app = create_app(scenarios_path=source, db_path=db, investigation_interpreter=slow, investigation_timeout_seconds=0.02)
    try:
        with TestClient(app) as client:
            _, auth, path = start(client, scenario)
            identity = str(uuid4())
            first = send(client, path, auth, request_id=identity)
            second = send(client, path, auth)
            third = send(client, path, auth)
            assert {first["mode"], second["mode"], third["mode"]} == {"fallback"}
            assert len(calls) == 2 and len(app.state.lab_investigation.gate.active) == 2
            assert send(client, path, auth, request_id=identity) == first
            assert len(calls) == 2
    finally:
        release.set()
    assert app.state.lab_investigation.gate.closed


def test_concurrent_same_uuid_and_new_choice_are_handled_without_overwrite(files):
    source, db, scenario = files
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow(payload):
        calls.append(payload)
        entered.set()
        release.wait(timeout=3)
        return {}

    app = create_app(scenarios_path=source, db_path=db, investigation_interpreter=slow)
    with TestClient(app) as client:
        session, auth, path = start(client, scenario)
        identity = str(uuid4())
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(send, client, path, auth, request_id=identity)
            assert entered.wait(timeout=2)
            try:
                duplicate = client.post(path, headers=auth, json={"request_id": identity})
                assert duplicate.status_code == 409
                revised = choice(client, session, auth, "revised", constraints={**scenario["defaults"], "budget_cents": 1400})
            finally:
                release.set()
            report = pending.result(timeout=3)
        assert report["stale"] is True
        assert report["anchor_choice_id"] != revised["id"]
        assert report["constraints"] == scenario["defaults"]
        assert send(client, path, auth, request_id=identity) == report
        assert len(calls) == 1
        assert len(client.get(f"/api/v1/sessions/{session['id']}", headers=auth).json()["choices"]) == 2


def test_expired_claim_can_resume_and_session_quota_preserves_retries(files):
    source, db, scenario = files
    app = create_app(scenarios_path=source, db_path=db)
    with TestClient(app) as client:
        session, auth, path = start(client, scenario)
        identity = str(uuid4())
        body = InvestigationRequest(request_id=identity)
        service = app.state.lab_investigation
        service._claim(session["id"], body)
        with app.state.lab_store.connect() as connection:
            connection.execute("UPDATE lab_investigations SET lease_until=0 WHERE session_id=?", (session["id"],))
        result = send(client, path, auth, request_id=identity)
        with app.state.lab_store.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM lab_investigations").fetchone()[0] == 1
            for _ in range(99):
                connection.execute(
                    """INSERT INTO lab_investigations
                    (id,session_id,request_id,request_hash,anchor_choice_id,created_at,response_json)
                    VALUES (?,?,?,?,?,?,?)""",
                    (str(uuid4()), session["id"], str(uuid4()), "seeded", result["anchor_choice_id"], result["created_at"], json.dumps(result)),
                )
        full = client.post(path, headers=auth, json={"request_id": str(uuid4())})
        assert full.status_code == 409 and "100-investigation" in full.text
        assert send(client, path, auth, request_id=identity) == result
