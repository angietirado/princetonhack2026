"""Terminal network boundaries use MockTransport, never private environment files."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from spending_api import terminal
from spending_api.config import Settings
from spending_api.models import Account, Snapshot, Transaction


def answer(status="answered"):
    return {
        "status": status,
        "answer": "Observed spending was $15.00; completeness is unknown.",
        "provider": "gemini",
        "model": "synthetic-model",
        "source": "fixture",
        "source_hash": "1" * 64,
        "model_calls": 2,
        "tool_calls": 1,
        "activity": [],
        "needed_inputs": [],
        "output_tokens_reserved": 0,
        "output_tokens_reported": 0,
        "evidence": [
            {
                "tool": "summarize_spending",
                "arguments": {},
                "result": {
                    "status": "ok",
                    "net_spending_cents": 1500,
                    "coverage": "observed_records_only",
                },
            }
        ],
        "caveats": ["Synthetic fixture; history completeness is unknown."],
    }


def setup_http(monkeypatch, handler, *, token="synthetic-app-token"):
    settings = Settings(
        _env_file=None,
        data_source="fixture",
        app_api_token=token,
        gemini_api_key="synthetic-provider-key-must-not-be-sent",
        llm_enabled=False,
    )
    monkeypatch.setattr(terminal, "load_settings", lambda: settings)

    async def no_snapshot(*_):
        pytest.fail("ask must not load a local snapshot")

    monkeypatch.setattr(terminal, "load_snapshot", no_snapshot)
    actual_client = httpx.Client

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"].read == settings.llm_timeout_seconds + 5
        return actual_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(terminal.httpx, "Client", client)


def test_ask_sends_only_question_and_app_token_then_prints_evidence(monkeypatch, capsys):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == terminal.ANSWER_URL
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-app-token"
        assert json.loads(request.content) == {"question": "Show November spending"}
        assert "synthetic-provider-key" not in str(request.headers) + request.content.decode()
        return httpx.Response(200, json=answer())

    setup_http(monkeypatch, handler)
    terminal.main(["ask", "  Show November spending  "])
    captured = capsys.readouterr()
    assert json.loads(captured.out) == answer()
    assert captured.err == "" and len(requests) == 1


@pytest.mark.parametrize("status", ["unavailable", "limited"])
def test_incomplete_answer_keeps_evidence_but_exits_nonzero(monkeypatch, capsys, status):
    setup_http(monkeypatch, lambda _: httpx.Response(200, json=answer(status)))
    with pytest.raises(SystemExit) as caught:
        terminal.main(["ask", "Summarize"])
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == status
    assert json.loads(captured.out)["evidence"] == answer()["evidence"]
    assert "completed answer" in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["ask"],
        ["ask", " "],
        ["ask", "x" * 2001],
        ["ask", "Summary", "--start", "2026-11-01"],
        ["ask", "Summary", "--end", "2026-11-13"],
        ["ask", "Summary", "--limit", "20"],
        ["summary", "Unexpected question"],
    ],
)
def test_invalid_cli_input_fails_before_settings_or_http(monkeypatch, capsys, argv):
    monkeypatch.setattr(terminal, "load_settings", lambda: pytest.fail("must validate first"))
    with pytest.raises(SystemExit) as caught:
        terminal.main(argv)
    assert caught.value.code == 2
    assert capsys.readouterr().out == ""


def test_missing_token_never_sends_request(monkeypatch, capsys):
    setup_http(monkeypatch, lambda _: pytest.fail("missing token must not send"), token="")
    with pytest.raises(SystemExit) as caught:
        terminal.main(["ask", "Summarize"])
    assert caught.value.code == 1
    assert "APP_API_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500, 503])
def test_http_errors_are_sanitized_and_not_retried(monkeypatch, capsys, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="PRIVATE_PROVIDER_KEY_OR_TRACEBACK")

    setup_http(monkeypatch, handler)
    with pytest.raises(SystemExit) as caught:
        terminal.main(["ask", "Summarize"])
    captured = capsys.readouterr()
    assert caught.value.code == 1 and len(requests) == 1
    assert captured.out == "" and "PRIVATE" not in captured.err


def test_redirect_does_not_forward_token(monkeypatch, capsys):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://unrelated.invalid/steal"})

    setup_http(monkeypatch, handler)
    with pytest.raises(SystemExit) as caught:
        terminal.main(["ask", "Summarize"])
    assert caught.value.code == 1 and len(requests) == 1
    assert "unrelated" not in capsys.readouterr().err


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_error_diagnostics_never_echo_credentials(monkeypatch, capsys, error):
    def handler(_):
        raise error("PRIVATE_APP_TOKEN_AND_REQUEST")

    setup_http(monkeypatch, handler)
    with pytest.raises(SystemExit) as caught:
        terminal.main(["ask", "Summarize"])
    captured = capsys.readouterr()
    assert caught.value.code == 1 and captured.out == ""
    assert "port 8765" in captured.err and "PRIVATE" not in captured.err


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"\xff",
        b"[]",
        b"{}",
        b'{"status":"success","private":"secret"}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_invalid_backend_answer_is_sanitized(monkeypatch, capsys, raw):
    setup_http(monkeypatch, lambda _: httpx.Response(200, content=raw))
    with pytest.raises(SystemExit) as caught:
        terminal.main(["ask", "Summarize"])
    captured = capsys.readouterr()
    assert caught.value.code == 1 and captured.out == ""
    assert "valid backend answer" in captured.err and "secret" not in captured.err


def test_oversized_response_is_bounded(monkeypatch, capsys):
    setup_http(monkeypatch, lambda _: httpx.Response(200, content=b" " * 512_001))
    with pytest.raises(SystemExit) as caught:
        terminal.main(["ask", "Summarize"])
    captured = capsys.readouterr()
    assert caught.value.code == 1 and captured.out == ""
    assert "size limit" in captured.err


def test_settings_failure_is_sanitized(monkeypatch, capsys):
    def fail():
        raise ValueError("PRIVATE_ENV_CONTENT")

    monkeypatch.setattr(terminal, "load_settings", fail)
    with pytest.raises(SystemExit) as caught:
        terminal.main(["ask", "Summarize"])
    captured = capsys.readouterr()
    assert caught.value.code == 1 and captured.out == "" and "PRIVATE" not in captured.err


@pytest.mark.parametrize("command", ["summary", "accounts", "transactions", "recurring"])
def test_existing_commands_still_use_local_deterministic_tools(monkeypatch, capsys, command):
    settings = Settings(_env_file=None, data_source="fixture")
    monkeypatch.setattr(terminal, "load_settings", lambda: settings)
    data = Snapshot(
        source="fixture",
        accounts=[
            Account(
                id="a",
                customer_id="c",
                nickname="Synthetic",
                type="checking",
                balance_cents=3000,
            )
        ],
        transactions=[
            Transaction(
                id="t",
                account_id="a",
                kind="purchase",
                status="completed",
                amount_cents=1500,
                occurred_at=datetime(2026, 11, 1, tzinfo=UTC),
                merchant="Example",
                category="dining",
            )
        ],
    )

    async def load(actual):
        assert actual is settings
        return data

    monkeypatch.setattr(terminal, "load_snapshot", load)
    monkeypatch.setattr(terminal.httpx, "Client", lambda **_: pytest.fail("unexpected HTTP"))
    terminal.main([command, "--start", "2026-11-01", "--end", "2026-11-01", "--limit", "1"])
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fixture"
    if command == "summary":
        assert output["net_spending_cents"] == 1500
    else:
        assert len(output["items"]) == (0 if command == "recurring" else 1)
