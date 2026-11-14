"""Photon 2.0 signed-event boundary and explicit bounded reply transport."""

import asyncio
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from spending_api.config import Settings

API_BASE = "https://api.photon.codes/v1"
EVENT_VERSION = "2026-07-01"
MAX_REPLY_CHARS = 1000
_ID = re.compile(r"^pho_(evt|msg|spc|usr|prj)_[0-7][0-9a-hjkmnp-tv-z]{25}$")


def valid_id(value, kind):
    return (
        isinstance(value, str) and bool(_ID.fullmatch(value)) and value.startswith(f"pho_{kind}_")
    )


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid Photon event")
    return value


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    space_id: str
    agent_id: str
    sender_id: str
    text: str
    platform: str
    project_id: str


def parse_incoming(event: dict, settings: Settings) -> IncomingMessage | None:
    """After signature verification, accept one enrolled direct plain-text channel."""
    event = _object(event)
    if event.get("type") != "message.received":
        return None
    data = _object(event.get("data"))
    message = _object(data.get("object"))
    platform = message.get("platform")
    if platform not in {"sms", "imessage"} or platform != settings.photon_platform:
        return None
    if (
        not valid_id(event.get("id"), "evt")
        or event.get("apiVersion") != EVENT_VERSION
        or event.get("source") != f"{platform}.photon"
        or not valid_id(data.get("projectId"), "prj")
        or data["projectId"] != settings.photon_project_id
    ):
        raise ValueError("Invalid Photon event scope or version")
    timestamp = event.get("timestamp")
    if not isinstance(timestamp, str):
        raise ValueError("Invalid Photon event timestamp")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("Invalid Photon event timestamp")
    sender = _object(message.get("sentBy"))
    receiver = _object(message.get("receivedBy"))
    space = _object(message.get("space"))
    content = _object(message.get("content"))
    if not all(item.get("platform") == platform for item in (sender, receiver, space)):
        raise ValueError("Invalid Photon channel scope")
    if not (
        message.get("status") == "received"
        and sender.get("kind") == "human"
        and sender.get("id") == settings.photon_allowed_sender_id
        and receiver.get("kind") == "agent"
        and receiver.get("id") == settings.photon_agent_user_id
        and sender.get("id") != receiver.get("id")
        and space.get("kind") == "direct"
        and content.get("type") == "text"
        and content.get("format") == "plain"
    ):
        return None
    identities = (
        (message.get("id"), "msg"),
        (space.get("id"), "spc"),
        (sender.get("id"), "usr"),
        (receiver.get("id"), "usr"),
    )
    if not all(valid_id(value, kind) for value, kind in identities):
        raise ValueError("Invalid Photon message identity")
    text = content.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("Invalid Photon message text")
    return IncomingMessage(
        message_id=message["id"],
        space_id=space["id"],
        agent_id=receiver["id"],
        sender_id=sender["id"],
        text=text,
        platform=platform,
        project_id=data["projectId"],
    )


def initialize_inbox(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS inbox (
            event_id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            received_at TEXT NOT NULL,
            scope_hash TEXT
        )
    """)
    if "scope_hash" not in {row[1] for row in connection.execute("PRAGMA table_info(inbox)")}:
        connection.execute("ALTER TABLE inbox ADD COLUMN scope_hash TEXT")


def scope_hash(settings):
    """Bind accepted work to channel enrollment and the configured financial source."""
    fields = (
        "photon_project_id",
        "photon_agent_user_id",
        "photon_allowed_sender_id",
        "photon_platform",
        "data_source",
        "nessie_customer_id",
        "nessie_base_url",
    )
    value = {key: getattr(settings, key) for key in fields}
    value["fixture_path"] = str(settings.fixture_path)
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def enqueue_event(
    path: Path, event_id: str, message_id: str, event: dict, *, accepted_scope_hash=None
) -> bool:
    """Persist before acknowledgment; replays dedupe, conflicting identities fail closed."""
    if not valid_id(event_id, "evt") or not valid_id(message_id, "msg"):
        raise ValueError("Invalid Photon identity")
    message = _object(_object(event.get("data")).get("object"))
    if event.get("id") != event_id or message.get("id") != message_id:
        raise ValueError("Conflicting Photon identity")
    payload = canonical(event)
    if len(payload.encode()) > 256_000:
        raise ValueError("Photon event exceeds storage limit")
    if accepted_scope_hash is not None and not re.fullmatch(r"[a-f0-9]{64}", accepted_scope_hash):
        raise ValueError("Invalid Photon scope binding")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
        initialize_inbox(connection)
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT event_id, message_id, payload FROM inbox WHERE event_id=? OR message_id=?",
            (event_id, message_id),
        ).fetchall()
        if previous:
            for old_event, old_message, old_payload in previous:
                old = json.loads(old_payload)
                keys = ("apiVersion", "source", "type", "data")
                if old_message != message_id or canonical(
                    {k: old.get(k) for k in keys}
                ) != canonical({k: event.get(k) for k in keys}):
                    raise ValueError("Conflicting Photon duplicate")
                if old_event == event_id and canonical(old) != payload:
                    raise ValueError("Conflicting Photon duplicate")
            return False
        connection.execute(
            "INSERT INTO inbox (event_id, message_id, payload, received_at, scope_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, message_id, payload, datetime.now(UTC).isoformat(), accepted_scope_hash),
        )
        return True


def reply_key(incoming):
    return f"spending-reply:{incoming.project_id}:{incoming.message_id}"


def reply_body(incoming, text):
    return {
        "spaceId": incoming.space_id,
        "senderId": incoming.agent_id,
        "parentMessageId": incoming.message_id,
        "content": {"type": "text", "format": "plain", "text": text},
    }


class PhotonSendError(Exception):
    """Only sanitized categories escape the provider boundary."""

    def __init__(self, category, *, retryable=False, uncertain=False):
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.uncertain = uncertain


async def send_reply(
    settings: Settings, incoming: IncomingMessage, text: str, *, transport=None
) -> dict:
    """One fixed-origin send. Persist body/key before invoking; 202 is not delivery."""
    identities = (
        (incoming.project_id, "prj"),
        (incoming.message_id, "msg"),
        (incoming.space_id, "spc"),
        (incoming.agent_id, "usr"),
        (incoming.sender_id, "usr"),
    )
    if (
        not settings.photon_enabled
        or not settings.photon_api_key.get_secret_value()
        or settings.photon_api_base_url.rstrip("/") != API_BASE
        or incoming.project_id != settings.photon_project_id
        or incoming.agent_id != settings.photon_agent_user_id
        or incoming.sender_id != settings.photon_allowed_sender_id
        or incoming.platform != settings.photon_platform
        or not all(valid_id(value, kind) for value, kind in identities)
    ):
        raise PhotonSendError("not_configured_or_scope_changed")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_REPLY_CHARS:
        raise PhotonSendError("invalid_reply")
    try:
        async with asyncio.timeout(15):
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=False, trust_env=False, transport=transport
            ) as client:
                async with client.stream(
                    "POST",
                    f"{API_BASE}/projects/{incoming.project_id}/messages",
                    headers={
                        "Authorization": f"Bearer {settings.photon_api_key.get_secret_value()}",
                        "Idempotency-Key": reply_key(incoming),
                        "Content-Type": "application/json",
                    },
                    content=canonical(reply_body(incoming, text)).encode(),
                ) as response:
                    if response.status_code != 202:
                        retryable = response.status_code == 429 or response.status_code >= 500
                        raise PhotonSendError(
                            "provider_retryable" if retryable else "provider_rejected",
                            retryable=retryable,
                            uncertain=retryable,
                        )
                    raw = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        raw.extend(chunk)
                        if len(raw) > 64_000:
                            raise PhotonSendError(
                                "invalid_provider_response", retryable=True, uncertain=True
                            )
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or not valid_id(value.get("id"), "msg")
            or value.get("status") not in {"dispatched", "accepted", "delivered", "failed"}
            or value.get("platform") != incoming.platform
            or _object(value.get("space")).get("id") != incoming.space_id
            or _object(value.get("sentBy")).get("id") != incoming.agent_id
        ):
            raise ValueError("Invalid provider acknowledgment")
        return {"id": value["id"], "status": value["status"], "accepted_by_api": True}
    except PhotonSendError:
        raise
    except (httpx.HTTPError, TimeoutError):
        raise PhotonSendError(
            "provider_connection_uncertain", retryable=True, uncertain=True
        ) from None
    except (ValueError, TypeError, KeyError, RecursionError):
        raise PhotonSendError("invalid_provider_response", retryable=True, uncertain=True) from None
