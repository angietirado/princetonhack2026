"""Conversational proposals remain bounded, persistent, and separate from experiment choices."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langsmith.run_helpers import get_tracing_context

from spending_api.lab_api import create_app
from spending_api.lab_chat import ChatRequest, InterpreterGate, offline_intent


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    monkeypatch.setenv("LAB_AI_ENABLED", "false")


@pytest.fixture
def chat_files(tmp_path):
    scenario = {
        "id": "chat-example", "version": "1", "name": "Chat example", "description": "A test",
        "home": {"label": "Home", "latitude": 0, "longitude": 0},
        "defaults": {
            "budget_cents": 5000, "time_budget_minutes": 60, "travel_mode": "walk", "max_stores": 2,
        },
        "basket": [{"id": "milk", "name": "Milk", "quantity": 1, "unit": "gallon", "category": "Dairy"}],
        "stores": [
            {"id": "near", "name": "Near", "latitude": 0, "longitude": 0.001,
             "shopping_minutes": 5, "prices": {"milk": 500}},
            {"id": "far", "name": "Far", "latitude": 0, "longitude": 0.01,
             "shopping_minutes": 5, "prices": {"milk": 300}},
        ],
        "travel": {
            "method": "modeled_distance", "road_factor": 1.1, "walk_kmh": 5,
            "drive_kmh": 30, "drive_cost_cents_per_km": 25, "drive_parking_minutes_per_stop": 3,
        },
        "provenance": {"prices": "Simulated"},
    }
    source, db = tmp_path / "scenarios.json", tmp_path / "chat.sqlite3"
    source.write_text(json.dumps({"schema_version": 1, "scenarios": [scenario]}), encoding="utf-8")
    return source, db, scenario


def start(client, scenario, *, initial=True):
    response = client.post("/api/v1/sessions", json={
        "request_id": str(uuid4()), "scenario_id": scenario["id"],
        "constraints": scenario["defaults"], "consent": True, "is_demo": False,
    })
    assert response.status_code == 201, response.text
    session = response.json()
    auth = {"Authorization": "Bearer " + session["token"]}
    if initial:
        assert client.post(
            f"/api/v1/sessions/{session['id']}/choices", headers=auth,
            json={"request_id": str(uuid4()), "stage": "initial", "assignments": {"milk": "near"}},
        ).status_code == 201
    return session, auth, f"/api/v1/sessions/{session['id']}/chat"


def send(client, path, auth, message, request_id=None):
    response = client.post(path, headers=auth, json={
        "request_id": request_id or str(uuid4()), "message": message,
    })
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(("message", "changes"), [
    ("one store only", {"max_stores": 1}),
    ("two stores", {"max_stores": 2}),
    ("I am walking", {"travel_mode": "walk"}),
    ("drive", {"travel_mode": "drive"}),
    ("under 30 minutes", {"time_budget_minutes": 30.0}),
    ("budget $60.25", {"budget_cents": 6025}),
    ("keep it under $50 and 25 minutes, one store only", {
        "budget_cents": 5000, "time_budget_minutes": 25.0, "max_stores": 1,
    }),
    ("no more than 40 minutes", {"time_budget_minutes": 40.0}),
    ("time limit 0.5 hours", {"time_budget_minutes": 30.0}),
])
def test_explicit_offline_commands(message, changes):
    assert offline_intent(message) == {"action": "update", "changes": changes}


@pytest.mark.parametrize("message", [
    "one store and vegan food", "one store 吃素", "walk or drive", "no walking", "not driving",
    "budget -$50", "budget $50 or $60", "at least 40 minutes", "over $50",
    "budget $999999999", "0 minutes", "three stores", "make my rent cheaper",
    "use my home price to infer my income", "under 20 or 30 minutes",
])
def test_unsupported_ambiguous_or_invalid_requests_do_not_partially_apply(message):
    assert offline_intent(message)["action"] == "clarify"


def test_chat_requires_session_auth_and_initial_choice(chat_files):
    source, db, scenario = chat_files
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        session, auth, path = start(client, scenario, initial=False)
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth).status_code == 409
        assert client.post(path, headers=auth, json={
            "request_id": str(uuid4()), "message": "one store",
        }).status_code == 409
        bad = [(b"authorization", b"Bearer \xff")]
        assert client.get(f"/api/v1/sessions/{session['id']}", headers=bad).status_code == 401
        assert client.get(path, headers=bad).status_code == 401


def test_non_ascii_admin_token_is_rejected_without_server_error(chat_files):
    source, db, _ = chat_files
    with TestClient(create_app(scenarios_path=source, db_path=db, admin_token="admin")) as client:
        assert client.get(
            "/api/v1/analytics/summary", headers=[(b"authorization", b"Bearer \xff")]
        ).status_code == 401


def test_offline_constraints_accumulate_and_choices_stay_unchanged(chat_files):
    source, db, scenario = chat_files
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        session, auth, path = start(client, scenario)
        one = send(client, path, auth, "one store only")
        assert one["mode"] == "offline"
        assert one["constraints"]["max_stores"] == 1
        two = send(client, path, auth, "under 20 minutes")
        assert two["constraints"]["max_stores"] == 1
        assert two["constraints"]["time_budget_minutes"] == 20
        assert two["status"] == "ok" and two["plans"]
        assert "returning home" in two["reply"]
        assert "save a revised choice" in two["reply"]
        history = client.get(path, headers=auth).json()
        assert len(history["messages"]) == 4
        assert [item["role"] for item in history["messages"]] == ["user", "assistant"] * 2
        original = client.get(f"/api/v1/sessions/{session['id']}", headers=auth).json()
        assert len(original["choices"]) == 1
        assert original["choices"][0]["evaluation"]["constraints"]["max_stores"] == 2
        unknown = send(client, path, auth, "one store and vegan food")
        assert unknown["status"] == "clarification"
        assert unknown["constraints"] == two["constraints"]
        assert unknown["plans"] == []


def test_chat_survives_restart_and_resets_after_new_saved_choice(chat_files):
    source, db, scenario = chat_files
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        session, auth, path = start(client, scenario)
        send(client, path, auth, "budget $70 and one store")
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        persisted = client.get(path, headers=auth).json()
        assert persisted["constraints"]["budget_cents"] == 7000
        assert len(persisted["messages"]) == 2
        revised = {**scenario["defaults"], "budget_cents": 8000, "time_budget_minutes": 45}
        assert client.post(
            f"/api/v1/sessions/{session['id']}/choices", headers=auth,
            json={"request_id": str(uuid4()), "stage": "revised",
                  "assignments": {"milk": "far"}, "constraints": revised},
        ).status_code == 201
        refreshed = client.get(path, headers=auth).json()
        assert refreshed["constraints"] == revised
        assert len(refreshed["messages"]) == 2
        proposal = send(client, path, auth, "one store")
        assert proposal["constraints"] == {**revised, "max_stores": 1}


def test_retry_is_durable_and_conflicting_message_is_rejected(chat_files):
    source, db, scenario = chat_files
    request_id = str(uuid4())
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        _, auth, path = start(client, scenario)
        first = send(client, path, auth, "one store", request_id)
        repeated = send(client, path, auth, "one store", request_id)
        assert repeated == first
        assert len(client.get(path, headers=auth).json()["messages"]) == 2
        assert client.post(path, headers=auth, json={
            "request_id": request_id, "message": "two stores",
        }).status_code == 409
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        assert send(client, path, auth, "one store", request_id) == first


def test_infeasible_chat_proposal_preserves_requested_limits(chat_files):
    source, db, scenario = chat_files
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        _, auth, path = start(client, scenario)
        result = send(client, path, auth, "budget $1 and 1 minute")
        assert result["status"] == "no_feasible_plan"
        assert result["plans"] == []
        assert result["constraints"]["budget_cents"] == 100
        assert result["constraints"]["time_budget_minutes"] == 1
        assert "limits unchanged" in result["reply"]


def test_fake_interpreter_one_call_bounded_history_and_tracing_disabled(chat_files, monkeypatch):
    source, db, scenario = chat_files
    calls = []
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    def interpreter(payload):
        calls.append((payload, get_tracing_context()["enabled"]))
        return {"action": "update", "changes": {"max_stores": 1}}

    with TestClient(create_app(
        scenarios_path=source, db_path=db, chat_interpreter=interpreter,
    )) as client:
        _, auth, path = start(client, scenario)
        for _ in range(4):
            result = send(client, path, auth, "Use a single destination for this trip.")
            assert result["mode"] == "model"
            assert result["constraints"]["max_stores"] == 1
        assert len(calls) == 4
        assert all(enabled is False for _, enabled in calls)
        payload = calls[-1][0]
        assert len(payload["recent_messages"]) == 4
        assert max(len(item["content"]) for item in payload["recent_messages"]) <= 300
        assert "scenario" not in payload and "token" not in payload and "prices" not in payload


@pytest.mark.parametrize("interpreter", [
    lambda _: (_ for _ in ()).throw(RuntimeError("secret-provider-key")),
    lambda _: {"action": "update", "changes": {"budget_cents": -50}},
    lambda _: {"action": "update", "changes": {"max_stores": 1}, "reply": "invented savings"},
    lambda _: "x" * 5000,
])
def test_model_failures_fall_back_without_leaking_or_inventing(chat_files, interpreter):
    source, db, scenario = chat_files
    with TestClient(create_app(
        scenarios_path=source, db_path=db, chat_interpreter=interpreter,
    )) as client:
        _, auth, path = start(client, scenario)
        result = send(client, path, auth, "one store")
        assert result["mode"] == "fallback"
        assert result["constraints"]["max_stores"] == 1
        assert "secret-provider-key" not in json.dumps(result)
        assert "invented savings" not in json.dumps(result)


def test_timeout_retains_real_worker_slots_until_work_finishes():
    release = threading.Event()
    calls = []

    def blocked(payload):
        calls.append(payload)
        release.wait(timeout=3)
        return {"action": "explain", "changes": {}}

    gate = InterpreterGate(blocked, timeout_seconds=0.02)

    async def exercise():
        try:
            with pytest.raises(TimeoutError):
                await gate.interpret({"request": 1})
            with pytest.raises(TimeoutError):
                await gate.interpret({"request": 2})
            with pytest.raises(RuntimeError, match="busy"):
                await gate.interpret({"request": 3})
            assert len(calls) == 2
        finally:
            release.set()
            await asyncio.sleep(0.05)
            gate.close()
    asyncio.run(exercise())


def test_model_timeout_returns_offline_result(chat_files):
    source, db, scenario = chat_files
    release = threading.Event()

    def slow(_):
        release.wait(timeout=3)
        return {"action": "update", "changes": {"max_stores": 2}}

    try:
        with TestClient(create_app(
            scenarios_path=source, db_path=db, chat_interpreter=slow, chat_timeout_seconds=0.02,
        )) as client:
            _, auth, path = start(client, scenario)
            result = send(client, path, auth, "one store")
            assert result["mode"] == "fallback"
            assert result["constraints"]["max_stores"] == 1
    finally:
        release.set()


def test_busy_session_does_not_run_second_interpreter(chat_files):
    source, db, scenario = chat_files
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow(payload):
        calls.append(payload)
        entered.set()
        release.wait(timeout=3)
        return {"action": "update", "changes": {"max_stores": 1}}

    with TestClient(create_app(
        scenarios_path=source, db_path=db, chat_interpreter=slow,
    )) as client:
        _, auth, path = start(client, scenario)
        request_id = str(uuid4())
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(send, client, path, auth, "one store", request_id)
            assert entered.wait(timeout=2)
            try:
                busy = client.post(path, headers=auth, json={
                    "request_id": str(uuid4()), "message": "two stores",
                })
                assert busy.status_code == 409
            finally:
                release.set()
            first = future.result(timeout=3)
        assert send(client, path, auth, "one store", request_id) == first
        assert len(calls) == 1


def test_new_choice_during_interpretation_cannot_be_overwritten(chat_files):
    source, db, scenario = chat_files
    entered, release = threading.Event(), threading.Event()

    def slow(_):
        entered.set()
        release.wait(timeout=3)
        return {"action": "update", "changes": {"max_stores": 1}}

    with TestClient(create_app(
        scenarios_path=source, db_path=db, chat_interpreter=slow,
    )) as client:
        session, auth, path = start(client, scenario)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(send, client, path, auth, "one store")
            assert entered.wait(timeout=2)
            changed = {**scenario["defaults"], "budget_cents": 8000}
            try:
                assert client.post(
                    f"/api/v1/sessions/{session['id']}/choices", headers=auth,
                    json={"request_id": str(uuid4()), "stage": "revised",
                          "assignments": {"milk": "far"}, "constraints": changed},
                ).status_code == 201
            finally:
                release.set()
            result = future.result(timeout=3)
        assert result["status"] == "clarification"
        assert result["constraints"] == changed


@pytest.mark.parametrize("message", ["", "   ", "x" * 1501, "\x01bad"])
def test_invalid_chat_messages_are_rejected(chat_files, message):
    source, db, scenario = chat_files
    with TestClient(create_app(scenarios_path=source, db_path=db)) as client:
        _, auth, path = start(client, scenario)
        assert client.post(path, headers=auth, json={
            "request_id": str(uuid4()), "message": message,
        }).status_code == 422
        assert client.get(path, headers=auth).json()["messages"] == []


def test_abandoned_lease_can_resume_without_duplicate_messages(chat_files):
    source, db, scenario = chat_files
    app = create_app(scenarios_path=source, db_path=db)
    with TestClient(app) as client:
        session, auth, path = start(client, scenario)
        request_id = str(uuid4())
        body = ChatRequest(request_id=request_id, message="one store")
        app.state.lab_chat._claim(session["id"], body)
        with app.state.lab_store.connect() as connection:
            connection.execute(
                "UPDATE lab_conversations SET busy_until=0 WHERE session_id=?", (session["id"],)
            )
        result = send(client, path, auth, "one store", request_id)
        assert result["status"] == "ok"
        assert len(client.get(path, headers=auth).json()["messages"]) == 2


