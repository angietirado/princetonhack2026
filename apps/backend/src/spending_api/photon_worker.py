"""One bounded Photon quick-control job; sending always requires explicit opt-in."""

import argparse
import asyncio
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import date

from spending_api.agent_tools import SpendingTools
from spending_api.config import load_settings
from spending_api.llm import _bound_tools
from spending_api.models import DateRange
from spending_api.nessie import load_snapshot
from spending_api.photon import (
    MAX_REPLY_CHARS,
    PhotonSendError,
    canonical,
    initialize_inbox,
    parse_incoming,
    reply_body,
    reply_key,
    scope_hash,
    send_reply,
)

HELP = (
    "Read-only controls: /help, /status, /accounts, "
    "/summary YYYY-MM-DD YYYY-MM-DD, /recurring YYYY-MM-DD YYYY-MM-DD. "
    "Dates are inclusive UTC. No payments or account changes."
)
LEASE_SECONDS = 90
MAX_ATTEMPTS = 3
RETRY_WINDOW_SECONDS = 600


def _money(cents):
    sign = "-" if cents < 0 else ""
    value = abs(cents)
    return f"{sign}${value // 100:,}.{value % 100:02} USD"


async def quick_reply(text, settings, *, snapshot_loader=load_snapshot):
    """Deterministic commands; no natural-language model or arbitrary tool selection."""
    parts = text.strip().split()
    command = parts[0].lower() if parts else ""
    if command == "/status" and len(parts) == 1:
        return (
            f"Read-only quick controls. Source: {settings.data_source}. "
            "No balance or history read performed.",
            None,
        )
    if command not in {"/summary", "/recurring", "/accounts"}:
        return HELP, None
    window = None
    if command == "/accounts":
        if len(parts) != 1:
            return HELP, None
    else:
        try:
            if len(parts) != 3 or any(len(value) != 10 for value in parts[1:]):
                raise ValueError
            window = DateRange(start=date.fromisoformat(parts[1]), end=date.fromisoformat(parts[2]))
            if window.start > window.end:
                raise ValueError
        except ValueError:
            return "Provide an ordered start and end date in YYYY-MM-DD format. " + HELP, None
    async with asyncio.timeout(20):
        snapshot = await snapshot_loader(settings)
    tools, source_hash = _bound_tools(SpendingTools(snapshot))
    source = "Synthetic fixture" if snapshot.source == "fixture" else "Observed account records"
    if command == "/accounts":
        cash = sum(
            a.balance_cents
            for a in tools.snapshot.accounts
            if a.type.casefold() in {"checking", "savings"}
        )
        text = (
            f"{source}: {len(tools.snapshot.accounts)} accounts; "
            f"checking/savings balance {_money(cash)}. Recorded snapshot, not a period total. "
            "Credit accounts excluded; freshness is unknown."
        )
    elif command == "/summary":
        result = tools.summarize_spending(window)
        text = (
            f"{source}; {window.start} through {window.end} UTC. "
            f"Observed net spending: {_money(result.net_spending_cents)}. "
            f"{result.included_count} included records; {result.excluded_count} excluded. "
            "Completed expenses minus refunds; pending activity, transfers, deposits "
            "and cash withdrawals excluded. "
            "Missing history is not proof of zero spending."
        )
    else:
        rows = tools.find_recurring_charges(window)
        descriptions = [
            f"{r.merchant[:60]}: {_money(r.amount_cents)}, {r.occurrences} occurrences"
            for r in rows[:3]
        ]
        text = (
            f"{source}; {window.start} through {window.end} UTC. "
            f"{len(rows)} monthly-pattern candidates; showing {min(3, len(rows))}. "
            + "; ".join(descriptions)
            + ". Candidates are not confirmed subscriptions or future bills; observed history only."
        )
    return text[:MAX_REPLY_CHARS], source_hash


@contextmanager
def _connect(settings):
    settings.photon_inbox_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.photon_inbox_path, timeout=5)
    try:
        db.row_factory = sqlite3.Row
        initialize_inbox(db)
        db.execute("""CREATE TABLE IF NOT EXISTS quick_replies (
            message_id TEXT PRIMARY KEY, state TEXT NOT NULL,
            lease TEXT, lease_until REAL NOT NULL DEFAULT 0,
            text TEXT, body_hash TEXT, idempotency_key TEXT, source_hash TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
            prepared_at REAL, provider_id TEXT, last_error TEXT
        )""")
        db.commit()
        with db:
            yield db
    finally:
        db.close()


def _claim(settings, *, send, now):
    lease = secrets.token_hex(16)
    with _connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        # Seed jobs without changing existing inbox records or prepared replies.
        db.execute(
            "INSERT OR IGNORE INTO quick_replies (message_id, state) "
            "SELECT message_id, 'queued' FROM inbox WHERE status='queued'"
        )
        states = (
            ("queued", "preparing", "prepared", "sending", "retry")
            if send
            else ("queued", "preparing")
        )
        marks = ",".join("?" for _ in states)
        row = db.execute(
            "SELECT i.payload, i.scope_hash, q.* "
            "FROM quick_replies q JOIN inbox i USING(message_id) "
            f"WHERE q.state IN ({marks}) AND q.lease_until<=? AND q.next_attempt<=? "
            "ORDER BY i.received_at, q.message_id LIMIT 1",
            (*states, now, now),
        ).fetchone()
        if row is None:
            return None
        state = "preparing" if row["text"] is None else "sending"
        db.execute(
            "UPDATE quick_replies SET state=?, lease=?, lease_until=? WHERE message_id=?",
            (state, lease, now + LEASE_SECONDS, row["message_id"]),
        )
        return dict(row), lease


def _save(settings, message_id, lease, **changes):
    # Column names originate exclusively in this module, never messages.
    allowed = {
        "state",
        "lease_until",
        "text",
        "body_hash",
        "idempotency_key",
        "source_hash",
        "attempts",
        "next_attempt",
        "prepared_at",
        "provider_id",
        "last_error",
    }
    if not changes.keys() <= allowed:
        raise ValueError("Invalid worker update")
    with _connect(settings) as db:
        cursor = db.execute(
            "UPDATE quick_replies SET "
            + ",".join(f"{key}=?" for key in changes)
            + " WHERE message_id=? AND lease=?",
            (*changes.values(), message_id, lease),
        )
        if cursor.rowcount != 1:
            raise ValueError("Worker lease was replaced")


async def process_once(
    settings, *, send=False, snapshot_loader=load_snapshot, transport=None, now=None
):
    """Prepare at most one command; send only with explicit send=True.

    Persist the exact body hash and key before the first attempt. An uncertain
    attempt reuses them within a short bounded retry window, never a fresh key.
    """
    if not settings.photon_enabled:
        return {"status": "disabled"}
    now = time.time() if now is None else now
    claimed = _claim(settings, send=send, now=now)
    if claimed is None:
        return {"status": "idle"}
    row, lease = claimed
    identity = row["message_id"]

    def save(**changes):
        _save(settings, identity, lease, **changes)

    try:
        if row["scope_hash"] != scope_hash(settings):
            raise ValueError("scope_changed")
        incoming = parse_incoming(json.loads(row["payload"]), settings)
        if incoming is None or incoming.message_id != identity:
            raise ValueError("scope_changed")
        text = row["text"]
        if text is None:
            try:
                text, source = await quick_reply(
                    incoming.text, settings, snapshot_loader=snapshot_loader
                )
            except Exception:
                # No exception/provider text or partial financial result is sent.
                text, source = (
                    "Account data is unavailable. No financial total was produced. "
                    "Try again later.",
                    None,
                )
            body_hash = hashlib.sha256(canonical(reply_body(incoming, text)).encode()).hexdigest()
            key = reply_key(incoming)
            save(
                state="prepared",
                text=text,
                body_hash=body_hash,
                idempotency_key=key,
                source_hash=source,
                prepared_at=now,
                lease_until=now + LEASE_SECONDS if send else 0,
            )
            row.update(text=text, body_hash=body_hash, idempotency_key=key, prepared_at=now)
        if not send:
            return {"status": "prepared", "sent": False}
        if (
            not isinstance(text, str)
            or len(text) > MAX_REPLY_CHARS
            or hashlib.sha256(canonical(reply_body(incoming, text)).encode()).hexdigest()
            != row["body_hash"]
            or reply_key(incoming) != row["idempotency_key"]
        ):
            raise ValueError("prepared_reply_changed")
        if row["attempts"] >= MAX_ATTEMPTS or (
            row["attempts"] and now - row["prepared_at"] > RETRY_WINDOW_SECONDS
        ):
            save(state="uncertain", last_error="retry_budget_exhausted", lease_until=0)
            return {"status": "uncertain", "sent": None}
        attempts = row["attempts"] + 1
        save(state="sending", attempts=attempts)
        try:
            accepted = await send_reply(settings, incoming, text, transport=transport)
        except PhotonSendError as error:
            retry = error.retryable and attempts < MAX_ATTEMPTS
            state = "retry" if retry else "uncertain" if error.uncertain else "blocked"
            save(
                state=state,
                last_error=error.category,
                next_attempt=now + 30 * attempts,
                lease_until=0,
            )
            return {
                "status": state,
                "sent": None if error.uncertain else False,
                "reason": error.category,
            }
        state = "failed" if accepted["status"] == "failed" else "accepted"
        save(state=state, provider_id=accepted["id"], last_error=None, lease_until=0)
        return {
            "status": state,
            "accepted_by_api": True,
            "delivery_confirmed": accepted["status"] == "delivered",
        }
    except (ValueError, TypeError, KeyError):
        save(state="blocked", last_error="scope_or_reply_invalid", lease_until=0)
        return {"status": "blocked", "sent": False, "reason": "scope_or_reply_invalid"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        required=True,
        help="Process at most one queued command; no background loop",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Explicitly send one persisted reply to Photon; omitted by default",
    )
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(process_once(load_settings(), send=args.send))
    except (OSError, sqlite3.Error, ValueError):
        result = {"status": "unavailable", "reason": "worker_configuration_or_storage"}
    print(json.dumps(result))
    return 1 if result["status"] in {"blocked", "uncertain", "unavailable", "failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
