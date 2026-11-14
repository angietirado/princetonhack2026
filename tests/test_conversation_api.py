"""HTTP context lifecycle with scripted graphs and synthetic in-memory snapshots."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_conversation_llm import ConversationModel, context_payload, synthetic_snapshot

from spending_api import main
from spending_api.config import Settings
from spending_api.conversation import ConversationStore

HEADERS = {"Authorization": "Bearer synthetic-app-token"}
ANSWER = "/api/v1/agent/answer"
CLEAR = "/api/v1/agent/context/clear"


def configured(**changes):
    values = {
        "_env_file": None, "llm_enabled": True, "data_source": "fixture",
        "gemini_api_key": "synthetic-provider-key", "app_api_token": "synthetic-app-token",
        "nessie_api_key": "synthetic-nessie-key", "nessie_customer_id": "synthetic-customer",
    }
    return Settings(**{**values, **changes})


def application(monkeypatch, *, model=None, settings=None, snapshot=None):
    model = model or ConversationModel()
    settings = settings or configured()
    source = {
        "snapshot": snapshot if snapshot is not None else synthetic_snapshot(), "reads": 0,
    }

    async def load(_settings):
        source["reads"] += 1
        return source["snapshot"].model_copy(deep=True)

    monkeypatch.setattr(main, "load_snapshot", load)
    return main.create_app(settings, agent_model=model), model, settings, source


def payload(question="Original goal", *, receipt=None, new=False):
    body = {"question": question}
    if new:
        body["conversation"] = {"mode": "new"}
    elif receipt is not None:
        body["conversation"] = {
            "mode": "continue", "id": receipt["id"], "revision": receipt["revision"],
        }
    return body


def active(response, turns=1):
    assert response.status_code == 200
    body = response.json()
    receipt = body["conversation"]
    assert set(receipt) == {"state", "id", "revision", "turns_used", "expires_at"}
    assert receipt["state"] == "active"
    assert re.fullmatch(r"[a-f0-9]{64}", receipt["id"])
    assert type(receipt["revision"]) is int and receipt["revision"] == turns
    assert receipt["turns_used"] == turns
    assert datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00")).tzinfo
    assert response.headers["cache-control"] == "no-store"
    serialized = json.dumps(receipt)
    assert "synthetic-customer" not in serialized and "synthetic-app-token" not in serialized
    return receipt


def test_question_only_requests_remain_stateless_and_omit_null_conversation(monkeypatch):
    class NoLegacyAllocation(ConversationStore):
        def begin(self, *args, **kwargs):
            pytest.fail("legacy request allocated conversation state")

    monkeypatch.setattr(main, "ConversationStore", NoLegacyAllocation)
    app, model, _, _ = application(monkeypatch)
    with TestClient(app) as client:
        first = client.post(ANSWER, headers=HEADERS, json=payload("First standalone question"))
        second = client.post(ANSWER, headers=HEADERS, json=payload("Second standalone question"))
    assert first.status_code == second.status_code == 200
    assert "conversation" not in first.json() and "conversation" not in second.json()
    users = [m.content for m in model.initial_messages[1] if m.type == "human"]
    assert users == ["Second standalone question"]


def test_explicit_follow_up_receives_only_user_context_and_fresh_evidence(monkeypatch):
    app, model, _, source = application(monkeypatch)
    with TestClient(app) as client:
        first = client.post(ANSWER, headers=HEADERS, json=payload("Original goal", new=True))
        receipt = active(first)
        second = client.post(
            ANSWER, headers=HEADERS, json=payload("What about groceries?", receipt=receipt),
        )
        next_receipt = active(second, turns=2)
    assert next_receipt["id"] == receipt["id"]
    assert source["reads"] == 2
    assert context_payload(model.initial_messages[1], "Original goal") == {
        "questions": ["Original goal"], "needed_inputs": [],
    }
    assert "CURRENT_ASSISTANT_SENTINEL" not in repr(model.initial_messages[1])
    assert "net_spending_cents" not in repr(model.initial_messages[1])
    for result in (first.json(), second.json()):
        assert result["status"] == "answered" and len(result["evidence"]) == 1
        assert result["evidence"][0]["source_hash"] == result["source_hash"]
        assert result["model_calls"] == 4 and result["tool_calls"] == 2


def test_clarification_reply_preserves_goal_without_model_written_claim(monkeypatch):
    model = ConversationModel(mode="clarification")
    app, _, _, _ = application(monkeypatch, model=model)
    with TestClient(app) as client:
        first = client.post(ANSWER, headers=HEADERS, json=payload("Compare spending", new=True))
        receipt = active(first)
        assert first.json()["status"] == "clarification"
        model.mode = "answered"
        reply = client.post(
            ANSWER, headers=HEADERS,
            json=payload("November 1-7 and November 8-14, 2026.", receipt=receipt),
        )
        active(reply, turns=2)
    assert context_payload(model.initial_messages[1], "Compare spending") == {
        "questions": ["Compare spending"], "needed_inputs": ["date_range"],
    }
    assert "MODEL_CLAIM_$999" not in repr(model.initial_messages[1])


@pytest.mark.parametrize("body", [
    {"question": "Q", "history": []},
    {"question": "Q", "customer_id": "private-customer"},
    {"question": "Q", "conversation": {"mode": "new", "id": "a" * 64}},
    {"question": "Q", "conversation": {"mode": "new", "messages": []}},
    {"question": "Q", "conversation": {"mode": "continue", "id": "a" * 64}},
    {"question": "Q", "conversation": {"mode": "continue", "id": "g" * 64, "revision": 1}},
    {"question": "Q", "conversation": {"mode": "continue", "id": "a" * 63, "revision": 1}},
    {"question": "Q", "conversation": {"mode": "continue", "id": "a" * 64, "revision": True}},
    {"question": "Q", "conversation": {"mode": "continue", "id": "a" * 64, "revision": 1.0}},
    {"question": "Q", "conversation": {"mode": "continue", "id": "a" * 64, "revision": "1"}},
    {"question": "Q", "conversation": {"mode": "continue", "id": "a" * 64, "revision": 0}},
    {"question": "Q", "conversation": {
        "mode": "continue", "id": "a" * 64, "revision": 1, "source_hash": "caller-chosen",
    }},
])
def test_invalid_context_wire_input_never_loads_source_or_calls_provider(monkeypatch, body):
    app, model, _, source = application(monkeypatch)
    with TestClient(app) as client:
        response = client.post(ANSWER, headers=HEADERS, json=body)
    assert response.status_code == 422
    assert source["reads"] == 0 and model.calls == 0
    assert "private-customer" not in response.text and "caller-chosen" not in response.text


@pytest.mark.parametrize("route", [ANSWER, CLEAR])
def test_auth_precedes_malformed_context_parsing_and_source_access(monkeypatch, route):
    app, model, _, source = application(monkeypatch)
    with TestClient(app) as client:
        response = client.post(route, content="private-invalid-json")
    assert response.status_code == 401
    assert source["reads"] == 0 and model.calls == 0
    assert "private-invalid-json" not in response.text


def test_unknown_context_is_visible_reset_without_provider_fallback(monkeypatch):
    app, model, _, _ = application(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            ANSWER, headers=HEADERS,
            json=payload("And what about it?", receipt={"id": "a" * 64, "revision": 1}),
        )
    assert response.status_code == 409 and model.calls == 0
    assert "Start a new question" in response.text


def test_stale_revision_does_not_replay_a_committed_turn(monkeypatch):
    app, model, _, _ = application(monkeypatch)
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        active(client.post(ANSWER, headers=HEADERS, json=payload("Second", receipt=first)), turns=2)
        calls = model.calls
        replay = client.post(ANSWER, headers=HEADERS, json=payload("Replay", receipt=first))
    assert replay.status_code == 409 and model.calls == calls


def test_changed_snapshot_requires_restart_before_model_call(monkeypatch):
    app, model, _, source = application(monkeypatch)
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        source["snapshot"].transactions[0].amount_cents += 100
        calls = model.calls
        response = client.post(ANSWER, headers=HEADERS, json=payload("Continue", receipt=first))
    assert response.status_code == 409 and model.calls == calls


def test_empty_fixture_scope_is_visible_stateless_fallback(monkeypatch):
    app, _, _, _ = application(monkeypatch, snapshot=synthetic_snapshot(empty=True))
    with TestClient(app) as client:
        response = client.post(ANSWER, headers=HEADERS, json=payload(new=True))
    assert response.status_code == 200
    receipt = response.json()["conversation"]
    assert receipt["state"] == "closed" and receipt["reason"] == "scope_unavailable"
    assert "id" not in receipt


def test_continuation_into_empty_fixture_scope_does_not_reach_provider(monkeypatch):
    app, model, _, source = application(monkeypatch)
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        source["snapshot"] = synthetic_snapshot(empty=True)
        calls = model.calls
        response = client.post(ANSWER, headers=HEADERS, json=payload("Continue", receipt=first))
    assert response.status_code == 409 and model.calls == calls


def test_empty_live_snapshot_still_binds_the_configured_customer(monkeypatch):
    config = configured(data_source="nessie", nessie_customer_id="first-customer")
    app, model, _, _ = application(
        monkeypatch, settings=config, snapshot=synthetic_snapshot(source="nessie", empty=True),
    )
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        config.nessie_customer_id = "second-customer"
        calls = model.calls
        response = client.post(ANSWER, headers=HEADERS, json=payload("Continue", receipt=first))
    assert response.status_code == 409 and model.calls == calls


def test_token_change_does_not_inherit_an_old_conversation(monkeypatch):
    app, model, config, _ = application(monkeypatch)
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        config.app_api_token = SecretStr("replacement-synthetic-token")
        calls = model.calls
        response = client.post(
            ANSWER, headers={"Authorization": "Bearer replacement-synthetic-token"},
            json=payload("Continue", receipt=first),
        )
    assert response.status_code == 409 and model.calls == calls


def test_source_selector_change_resets_even_when_snapshot_hash_is_identical(monkeypatch, tmp_path):
    app, model, config, _ = application(monkeypatch)
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        config.fixture_path = tmp_path / "another-unread-source.json"
        calls = model.calls
        response = client.post(ANSWER, headers=HEADERS, json=payload("Continue", receipt=first))
    assert response.status_code == 409 and model.calls == calls


def test_expired_context_uses_injected_clock_and_never_falls_back_to_a_model(monkeypatch):
    elapsed = [0.0]
    origin = datetime(2026, 11, 14, tzinfo=UTC)

    def store_factory(*args, **kwargs):
        return ConversationStore(
            clock=lambda: elapsed[0], wall_clock=lambda: origin + timedelta(seconds=elapsed[0]),
        )

    monkeypatch.setattr(main, "ConversationStore", store_factory)
    app, model, _, _ = application(monkeypatch)
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        elapsed[0] = 600
        calls = model.calls
        response = client.post(ANSWER, headers=HEADERS, json=payload("Continue", receipt=first))
    assert response.status_code == 409 and model.calls == calls


def test_clear_is_idempotent_and_does_not_read_source_or_call_model(monkeypatch):
    app, model, config, source = application(monkeypatch)
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        calls, reads = model.calls, source["reads"]
        config.llm_enabled = False
        for identifier in (first["id"], first["id"], "0" * 64):
            response = client.post(CLEAR, headers=HEADERS, json={"id": identifier})
            assert response.status_code == 204 and not response.content
        assert model.calls == calls and source["reads"] == reads
        config.llm_enabled = True
        expired = client.post(ANSWER, headers=HEADERS, json=payload("Continue", receipt=first))
    assert expired.status_code == 409 and model.calls == calls


@pytest.mark.parametrize("body", [
    {"id": "a" * 63}, {"id": True}, {"conversation_id": "a" * 64},
    {"id": "a" * 64, "authority": "caller-controlled"},
])
def test_clear_schema_does_not_accept_client_scope_or_malformed_ids(monkeypatch, body):
    app, model, _, source = application(monkeypatch)
    with TestClient(app) as client:
        response = client.post(CLEAR, headers=HEADERS, json=body)
    assert response.status_code == 422
    assert source["reads"] == 0 and model.calls == 0
    assert "caller-controlled" not in response.text


@pytest.mark.parametrize("mode", ["bare", "fail"])
def test_unsuccessful_follow_up_does_not_advance_or_contaminate_context(monkeypatch, mode):
    app, model, _, _ = application(monkeypatch)
    with TestClient(app) as client:
        first = active(client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
        model.mode = mode
        failed = client.post(
            ANSWER, headers=HEADERS, json=payload("Unsuccessful follow-up", receipt=first),
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "unavailable" and failed.json()["evidence"] == []
        assert failed.json()["conversation"] == first
        model.mode = "answered"
        retry = client.post(
            ANSWER, headers=HEADERS, json=payload("Replacement follow-up", receipt=first),
        )
        active(retry, turns=2)
    assert context_payload(model.initial_messages[-1], "Original goal") == {
        "questions": ["Original goal"], "needed_inputs": [],
    }
    assert "Unsuccessful follow-up" not in repr(model.initial_messages[-1])


def test_openapi_request_schemas_have_no_dangling_local_references(monkeypatch):
    app, _, _, _ = application(monkeypatch)
    with TestClient(app) as client:
        document = client.get("/openapi.json").json()

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value and value["$ref"].startswith("#/"):
                target = document
                for part in value["$ref"][2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    for route in (ANSWER, CLEAR):
        walk(document["paths"][route]["post"]["requestBody"])


class PausingModel(ConversationModel):
    def __init__(self):
        super().__init__()
        self.pause = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def advisor(self, messages, config=None):
        if self.pause and messages[-1].type == "human":
            self.entered.set()
            await self.release.wait()
        return await super().advisor(messages, config=config)


def test_clear_during_a_pending_turn_prevents_server_history_resurrection(monkeypatch):
    async def scenario():
        model = PausingModel()
        app, _, _, _ = application(monkeypatch, model=model)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic",
        ) as client:
            first = active(await client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
            model.pause = True
            pending = asyncio.create_task(client.post(
                ANSWER, headers=HEADERS, json=payload("Pending follow-up", receipt=first),
            ))
            await asyncio.wait_for(model.entered.wait(), 3)
            busy = await client.post(
                ANSWER, headers=HEADERS, json=payload("Competing", receipt=first),
            )
            assert busy.status_code == 429
            cleared = await client.post(CLEAR, headers=HEADERS, json={"id": first["id"]})
            assert cleared.status_code == 204
            model.release.set()
            result = await asyncio.wait_for(pending, 3)
            assert result.status_code == 200
            assert result.json()["conversation"] == {
                "state": "closed", "reason": "cleared", "turns_used": 1,
            }
            calls = model.calls
            late = await client.post(ANSWER, headers=HEADERS, json=payload("Late", receipt=first))
            assert late.status_code == 409 and model.calls == calls

    asyncio.run(scenario())


def test_cancelled_http_turn_rolls_back_without_advancing_context(monkeypatch):
    async def scenario():
        model = PausingModel()
        app, _, _, _ = application(monkeypatch, model=model)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic",
        ) as client:
            first = active(await client.post(ANSWER, headers=HEADERS, json=payload(new=True)))
            model.pause = True
            pending = asyncio.create_task(client.post(
                ANSWER, headers=HEADERS, json=payload("Cancelled follow-up", receipt=first),
            ))
            await asyncio.wait_for(model.entered.wait(), 3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            model.pause = False
            model.release.set()
            retry = await client.post(
                ANSWER, headers=HEADERS, json=payload("Replacement follow-up", receipt=first),
            )
            active(retry, turns=2)
            assert context_payload(model.initial_messages[-1], "Original goal") == {
                "questions": ["Original goal"], "needed_inputs": [],
            }
            assert "Cancelled follow-up" not in repr(model.initial_messages[-1])

    asyncio.run(scenario())
