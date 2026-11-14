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
from spending_api.photon import parse_incoming, reply_key, scope_hash, send_reply

SECRET = "whsec_" + base64.b64encode(b"test-only-signing-secret-32-bytes").decode()
PROJECT = "pho_prj_01" + "0" * 24
AGENT = "pho_usr_01" + "0" * 23 + "1"
HUMAN = "pho_usr_01" + "0" * 23 + "2"
MESSAGE = "pho_msg_01" + "0" * 24
SPACE = "pho_spc_01" + "0" * 24
EVENT = "pho_evt_01" + "0" * 24


def settings(tmp_path):
    return Settings(
        _env_file=None,
        photon_enabled=True,
        photon_webhook_secret=SECRET,
        photon_project_id=PROJECT,
        photon_agent_user_id=AGENT,
        photon_allowed_sender_id=HUMAN,
        photon_inbox_path=tmp_path / "inbox.sqlite3",
    )


def event():
    return {
        "id": EVENT,
        "apiVersion": "2026-07-01",
        "source": "sms.photon",
        "timestamp": "2026-11-14T00:00:00Z",
        "type": "message.received",
        "data": {
            "projectId": PROJECT,
            "object": {
                "id": MESSAGE,
                "platform": "sms",
                "status": "received",
                "sentBy": {"id": HUMAN, "kind": "human", "platform": "sms"},
                "receivedBy": {"id": AGENT, "kind": "agent", "platform": "sms"},
                "space": {"id": SPACE, "kind": "direct", "platform": "sms"},
                "content": {"type": "text", "format": "plain", "text": "How much did I spend?"},
            },
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
            (MESSAGE, "queued"),
        ]
        assert db.execute("SELECT scope_hash FROM inbox").fetchone()[0] == scope_hash(config)


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


def test_outbound_transport_reuses_message_idempotency_key(tmp_path):
    config = settings(tmp_path)
    # Use validated SecretStr through Settings, never real credentials.
    config.photon_api_key = Settings(_env_file=None, photon_api_key="fake").photon_api_key
    incoming = parse_incoming(event(), config)
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["spaceId"] == SPACE
        assert body["parentMessageId"] == MESSAGE
        return httpx.Response(
            202,
            json={
                "id": MESSAGE[:-1] + "3",
                "status": "dispatched",
                "platform": "sms",
                "space": {"id": SPACE},
                "sentBy": {"id": AGENT},
            },
        )

    for _ in range(2):
        assert (
            asyncio.run(
                send_reply(config, incoming, "A test reply", transport=httpx.MockTransport(handler))
            )["status"]
            == "dispatched"
        )
    assert {r.headers["idempotency-key"] for r in requests} == {reply_key(incoming)}


@pytest.mark.parametrize(
    "field,value",
    [("apiVersion", "old"), ("source", "imessage.photon"), ("timestamp", "2026-11-14")],
)
def test_wrong_event_contract_rejected(tmp_path, field, value):
    payload = event()
    payload[field] = value
    body, headers = signed(payload)
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/webhooks/photon", content=body, headers=headers).status_code == 400


def test_wrong_project_and_nested_channel_rejected(tmp_path):
    for mode in ("project", "platform"):
        payload = event()
        if mode == "project":
            payload["data"]["projectId"] = PROJECT[:-1] + "3"
        else:
            payload["data"]["object"]["space"]["platform"] = "imessage"
        with pytest.raises(ValueError):
            parse_incoming(payload, settings(tmp_path))


def test_conflicting_duplicate_is_not_acknowledged(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        original = event()
        body, headers = signed(original)
        assert client.post("/webhooks/photon", content=body, headers=headers).status_code == 204
        original["data"]["object"]["content"]["text"] = "/accounts"
        body, headers = signed(original)
        assert client.post("/webhooks/photon", content=body, headers=headers).status_code == 400
