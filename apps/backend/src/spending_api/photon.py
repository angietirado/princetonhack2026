"""Photon 2.0 event boundary and explicit outbound transport."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from spending_api.config import Settings


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    space_id: str
    agent_id: str
    sender_id: str
    text: str
    platform: str


def parse_incoming(event: dict, settings: Settings) -> IncomingMessage | None:
    """Call only after signature verification. Ignore other senders, groups, and bot echoes."""
    if event.get("type") != "message.received":
        return None
    message = event.get("data", {}).get("object", {})
    sender = message.get("sentBy") or {}
    receiver = message.get("receivedBy") or {}
    space = message.get("space") or {}
    content = message.get("content") or {}
    if not (
        message.get("status") == "received"
        and message.get("platform") in {"sms", "imessage"}
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
    if not all(
        isinstance(value, str) and value
        for value in (
            message.get("id"),
            space.get("id"),
            content.get("text"),
        )
    ):
        raise ValueError("Missing message identity or text")
    if len(content["text"]) > 4000:
        raise ValueError("Message text exceeds the demo limit")
    return IncomingMessage(
        message_id=message["id"],
        space_id=space["id"],
        agent_id=receiver["id"],
        sender_id=sender["id"],
        text=content["text"],
        platform=message["platform"],
    )


def enqueue_event(path: Path, event_id: str, message_id: str, event: dict) -> bool:
    """Atomically persist the event and its queued status; return False for duplicates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=5) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inbox (
                event_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                received_at TEXT NOT NULL
            )
        """)
        cursor = connection.execute(
            "INSERT OR IGNORE INTO inbox (event_id, message_id, payload, received_at) "
            "VALUES (?, ?, ?, ?)",
            (event_id, message_id, json.dumps(event), datetime.now(UTC).isoformat()),
        )
        return cursor.rowcount == 1


async def send_reply(settings: Settings, incoming: IncomingMessage, text: str) -> dict:
    """Explicit send primitive for the future worker. Acceptance is not delivery."""
    if not settings.photon_api_key.get_secret_value() or not settings.photon_project_id:
        raise ValueError("Configure PHOTON_API_KEY and PHOTON_PROJECT_ID before sending")
    if not text.strip() or len(text) > 1000:
        raise ValueError("Replies must contain 1 to 1000 characters")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{settings.photon_api_base_url.rstrip('/')}/projects/"
            f"{settings.photon_project_id}/messages",
            headers={
                "Authorization": f"Bearer {settings.photon_api_key.get_secret_value()}",
                "Idempotency-Key": f"spending-reply:{incoming.message_id}",
            },
            json={
                "spaceId": incoming.space_id,
                "senderId": incoming.agent_id,
                "parentMessageId": incoming.message_id,
                "content": {"type": "text", "format": "plain", "text": text},
            },
        )
        response.raise_for_status()
        return response.json()
