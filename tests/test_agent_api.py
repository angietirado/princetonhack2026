import asyncio
import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from spending_api.config import Settings
from spending_api.main import create_app
from spending_generator.cli import build_fixture

HEADERS = {"Authorization": "Bearer local-test-token"}


class ScriptedModel:
    def __init__(self):
        self.calls = []
        self.tool_names = []

    def bind_tools(self, tools):
        self.tool_names = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages, config=None):
        self.calls.append(list(messages))
        if len(self.calls) % 2:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "summarize_spending",
                        "args": {"start": "2026-11-01", "end": "2026-11-13"},
                        "id": "sum-1",
                    }
                ],
            )
        evidence = json.loads(messages[-1].content)
        return AIMessage(content=f"Observed spending: {evidence['net_spending_cents']} cents.")


def configured(tmp_path, **overrides):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(build_fixture(42, 90, date(2026, 11, 13))), encoding="utf-8")
    options = dict(
        _env_file=None,
        data_source="fixture",
        fixture_path=fixture,
        llm_enabled=True,
        llm_provider="gemini",
        gemini_api_key="fake-provider-key",
        app_api_token="local-test-token",
    )
    options.update(overrides)
    return Settings(**options)


def test_http_answer_runs_real_langchain_tools_on_server_bound_data(tmp_path):
    model = ScriptedModel()
    with TestClient(create_app(configured(tmp_path), agent_model=model)) as client:
        response = client.post(
            "/api/v1/agent/answer",
            headers=HEADERS,
            json={"question": "Summarize November 1 through 13, 2026."},
        )
        assert response.status_code == 200
        result = response.json()
        evidence = result["evidence"][0]["result"]
        assert result["status"] == "answered"
        assert str(evidence["net_spending_cents"]) in result["answer"]
        assert result["model_calls"] == 2 and result["tool_calls"] == 1
        assert evidence["source"] == "fixture"
        assert evidence["coverage"] == "observed_records_only"
        assert len(result["source_hash"]) == 64
        assert response.headers["cache-control"] == "no-store"
        assert "fake-provider-key" not in response.text
        assert "local-test-token" not in response.text


def test_auth_precedes_json_parsing_and_snapshot_loading(tmp_path):
    settings = configured(tmp_path)
    settings.fixture_path = tmp_path / "missing"
    model = ScriptedModel()
    with TestClient(create_app(settings, agent_model=model)) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = client.post("/api/v1/agent/answer", content=b"invalid", headers=headers)
            assert response.status_code == 401
    assert model.calls == []


def test_non_ascii_and_duplicate_bearer_headers_are_rejected(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        for headers in (
            [(b"authorization", b"Bearer \xff")],
            [
                (b"authorization", b"Bearer local-test-token"),
                (b"authorization", b"Bearer local-test-token"),
            ],
        ):
            result = client.post(
                "/api/v1/agent/answer", headers=headers, json={"question": "Summarize"}
            )
            assert result.status_code == 401


@pytest.mark.parametrize(
    "failure",
    [
        KeyError("private-id"),
        ValueError("secret-value"),
        OSError("private-path"),
        TypeError("private-value"),
    ],
)
def test_invalid_snapshot_returns_sanitized_error(tmp_path, monkeypatch, failure):
    async def broken(_):
        raise failure

    monkeypatch.setattr("spending_api.main.load_snapshot", broken)
    with TestClient(create_app(configured(tmp_path))) as client:
        result = client.post(
            "/api/v1/agent/answer", headers=HEADERS, json={"question": "Summarize"}
        )
        assert result.status_code == 503
        assert result.json() == {"detail": "Customer data cannot support a grounded AI answer"}


def test_snapshot_loading_is_inside_total_request_deadline(tmp_path, monkeypatch):
    async def slow(_):
        await asyncio.sleep(10)

    monkeypatch.setattr("spending_api.main.load_snapshot", slow)
    settings = configured(tmp_path, llm_timeout_seconds=1)
    with TestClient(create_app(settings)) as client:
        result = client.post(
            "/api/v1/agent/answer", headers=HEADERS, json={"question": "Summarize"}
        )
        assert result.status_code == 504
        assert result.json() == {"detail": "AI request deadline exceeded"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": " "},
        {"question": "x" * 2001},
        {"question": True},
        {"question": "Read", "customer_id": "someone-else"},
        {"question": "Read", "provider": "nemotron"},
        {"question": "Read", "source_path": "private-file"},
    ],
)
def test_invalid_or_scope_changing_request_never_calls_provider(tmp_path, payload):
    model = ScriptedModel()
    with TestClient(create_app(configured(tmp_path), agent_model=model)) as client:
        response = client.post("/api/v1/agent/answer", json=payload, headers=HEADERS)
        assert response.status_code == 422
    assert model.calls == []


@pytest.mark.parametrize(
    "raw",
    [b"{} trailing", b"\xff", b"[]", b'{"question":"a","question":"b"}', b"[" * 2000 + b"]" * 2000],
)
def test_malformed_json_is_generic_and_does_not_echo_input(tmp_path, raw):
    with TestClient(create_app(configured(tmp_path), agent_model=ScriptedModel())) as client:
        response = client.post(
            "/api/v1/agent/answer",
            content=raw,
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid JSON request"}


def test_body_and_media_limits(tmp_path):
    with TestClient(create_app(configured(tmp_path), agent_model=ScriptedModel())) as client:
        assert (
            client.post("/api/v1/agent/answer", content="text", headers=HEADERS).status_code == 415
        )
        oversized = client.post(
            "/api/v1/agent/answer",
            content=b" " * 8193,
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        assert oversized.status_code == 413


def test_question_rejects_query_parameter_scope_overrides(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        response = client.post(
            "/api/v1/agent/answer?customer_id=other",
            headers=HEADERS,
            json={"question": "Summarize"},
        )
        assert response.status_code == 422


def test_disabled_status_does_not_initialize_model_or_read_fixture(tmp_path):
    settings = Settings(
        _env_file=None,
        data_source="fixture",
        llm_enabled=False,
        fixture_path=tmp_path / "missing",
        app_api_token="local-test-token",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/agent/status").status_code == 401
        status = client.get("/api/v1/agent/status", headers=HEADERS)
        assert status.json()["enabled"] is False
        assert "key" not in status.text
        assert (
            client.post(
                "/api/v1/agent/answer", headers=HEADERS, json={"question": "Anything"}
            ).status_code
            == 503
        )


def test_cors_allows_authenticated_post_from_configured_origin(tmp_path):
    with TestClient(create_app(configured(tmp_path))) as client:
        response = client.options(
            "/api/v1/agent/answer",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_busy_agent_rejects_parallel_request_and_releases_slot(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowModel(ScriptedModel):
            async def ainvoke(self, messages, config=None):
                entered.set()
                await release.wait()
                return await super().ainvoke(messages, config)

        app = create_app(configured(tmp_path), agent_model=SlowModel())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = asyncio.create_task(
                client.post("/api/v1/agent/answer", headers=HEADERS, json={"question": "Summarize"})
            )
            await asyncio.wait_for(entered.wait(), 3)
            busy = await client.post(
                "/api/v1/agent/answer", headers=HEADERS, json={"question": "Summarize"}
            )
            assert busy.status_code == 429
            release.set()
            assert (await first).status_code == 200
            again = await client.post(
                "/api/v1/agent/answer", headers=HEADERS, json={"question": "Summarize"}
            )
            assert again.status_code == 200

    asyncio.run(run())
