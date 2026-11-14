import asyncio
import base64
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from standardwebhooks.webhooks import Webhook

from spending_api.config import Settings
from spending_api.main import create_app
from spending_api.photon import parse_incoming, send_reply

SECRET = "whsec_" + base64.b64encode(b"test-only-signing-secret-32-bytes").decode()


def settings(tmp_path):
    return Settings(
        _env_file=None,
        photon_webhook_secret=SECRET,
        photon_agent_user_id="pho_usr_agent",
        photon_allowed_sender_id="pho_usr_human",
        photon_inbox_path=tmp_path / "inbox.sqlite3",
    )


def event():
    return {
        "id": "pho_evt_test",
        "type": "message.received",
        "data": {
            "object": {
                "id": "pho_msg_test",
                "platform": "sms",
                "status": "received",
                "sentBy": {"id": "pho_usr_human", "kind": "human"},
                "receivedBy": {"id": "pho_usr_agent", "kind": "agent"},
                "space": {"id": "pho_spc_test", "kind": "direct"},
                "content": {"type": "text", "format": "plain", "text": "How much did I spend?"},
            }
        },
    }


def signed(payload, timestamp=None):
    timestamp = timestamp or datetime.now(UTC)
    body = json.dumps(payload)
    return body, {
        "content-type": "application/json",
        "webhook-id": payload["id"],
        "webhook-timestamp": str(int(timestamp.timestamp())),
        "webhook-signature": Webhook(SECRET).sign(payload["id"], timestamp, body),
    }


def test_signed_event_persists_once_across_app_restarts(tmp_path):
    config = settings(tmp_path)
    body, headers = signed(event())
    for _ in range(2):
        with TestClient(create_app(config)) as client:
            assert client.post("/webhooks/photon", content=body, headers=headers).status_code == 204
    with sqlite3.connect(config.photon_inbox_path) as db:
        assert db.execute("SELECT message_id, status FROM inbox").fetchall() == [
            ("pho_msg_test", "queued"),
        ]


@pytest.mark.parametrize("kind", ["tampered", "stale", "unsigned"])
def test_invalid_webhooks_do_not_store_data(tmp_path, kind):
    config = settings(tmp_path)
    body, headers = signed(
        event(), datetime.now(UTC) - timedelta(hours=1) if kind == "stale" else None
    )
    if kind == "tampered":
        body = body.replace("How much", "How little")
    if kind == "unsigned":
        headers.pop("webhook-signature")
    with TestClient(create_app(config)) as client:
        assert client.post("/webhooks/photon", content=body, headers=headers).status_code == 400
    assert not config.photon_inbox_path.exists()


@pytest.mark.parametrize("change", ["sender", "group", "echo"])
def test_ignore_unenrolled_sender_groups_and_bot_echoes(tmp_path, change):
    payload = event()
    message = payload["data"]["object"]
    if change == "sender":
        message["sentBy"]["id"] = "pho_usr_stranger"
    elif change == "group":
        message["space"]["kind"] = "group"
    else:
        message["sentBy"] = message["receivedBy"]
    assert parse_incoming(payload, settings(tmp_path)) is None


def test_outbound_transport_reuses_message_idempotency_key(tmp_path, monkeypatch):
    config = settings(tmp_path).model_copy(update={"photon_project_id": "pho_prj_test"})
    # Use validated SecretStr through Settings, never real credentials.
    config.photon_api_key = Settings(_env_file=None, photon_api_key="fake").photon_api_key
    incoming = parse_incoming(event(), config)
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["spaceId"] == "pho_spc_test"
        assert body["parentMessageId"] == "pho_msg_test"
        return httpx.Response(202, json={"id": "pho_msg_reply", "status": "pending"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "spending_api.photon.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    for _ in range(2):
        assert asyncio.run(send_reply(config, incoming, "A test reply"))["status"] == "pending"
    assert {r.headers["idempotency-key"] for r in requests} == {"spending-reply:pho_msg_test"}
