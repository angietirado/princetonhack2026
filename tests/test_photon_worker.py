"""Offline worker acceptance tests; no private settings or message transport."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from spending_api.models import Account, Snapshot, Transaction
from spending_api.photon import enqueue_event, scope_hash
from spending_api.photon_worker import _claim, process_once, quick_reply
from tests.test_photon import AGENT, EVENT, MESSAGE, SPACE, event, settings


def queue(tmp_path, text="/summary 2026-11-01 2026-11-14"):
    config = settings(tmp_path)
    config.photon_api_key = SecretStr("offline-key")
    payload = event()
    payload["data"]["object"]["content"]["text"] = text
    enqueue_event(
        config.photon_inbox_path, EVENT, MESSAGE, payload, accepted_scope_hash=scope_hash(config)
    )
    return config


async def snapshot(_):
    return Snapshot(
        source="fixture",
        accounts=[
            Account(id="a", customer_id="c", nickname="Test", type="Checking", balance_cents=50000)
        ],
        transactions=[
            Transaction(
                id="p",
                account_id="a",
                kind="purchase",
                status="completed",
                amount_cents=1234,
                occurred_at=datetime(2026, 11, 5, tzinfo=UTC),
                merchant="Cafe",
                category="dining",
            ),
            Transaction(
                id="r",
                account_id="a",
                kind="refund",
                status="completed",
                amount_cents=234,
                occurred_at=datetime(2026, 11, 6, tzinfo=UTC),
                merchant="Cafe",
                category="dining",
            ),
        ],
    )


def row(config):
    with closing(sqlite3.connect(config.photon_inbox_path)) as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT * FROM quick_replies").fetchone())


def accepted(request):
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


def test_prepare_persists_exact_reply_without_sending_then_restart_uses_it(tmp_path):
    config = queue(tmp_path)
    result = asyncio.run(process_once(config, snapshot_loader=snapshot, now=1000))
    assert result == {"status": "prepared", "sent": False}
    saved = row(config)
    assert "$10.00 USD" in saved["text"] and "Synthetic fixture" in saved["text"]
    assert len(saved["source_hash"]) == 64 and saved["attempts"] == 0
    assert asyncio.run(process_once(config, now=1001))["status"] == "idle"

    async def forbidden(_):
        pytest.fail("Prepared reply must not be recalculated after snapshot changes")

    requests = []

    def transport(request):
        requests.append(request)
        assert json.loads(request.content)["content"]["text"] == saved["text"]
        return accepted(request)

    result = asyncio.run(
        process_once(
            config,
            send=True,
            snapshot_loader=forbidden,
            transport=httpx.MockTransport(transport),
            now=1002,
        )
    )
    assert result == {"status": "accepted", "accepted_by_api": True, "delivery_confirmed": False}
    assert asyncio.run(process_once(config, send=True, now=1003))["status"] == "idle"
    assert len(requests) == 1


def test_uncertain_send_reuses_same_key_and_body_with_bounded_retry(tmp_path):
    config = queue(tmp_path, "/help")
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("private provider text must not escape")

    for at in (1000, 1031, 1092):
        result = asyncio.run(
            process_once(config, send=True, transport=httpx.MockTransport(timeout), now=at)
        )
        assert "private" not in json.dumps(result)
    assert result["status"] == "uncertain"
    assert len({r.content for r in requests}) == 1
    assert len({r.headers["idempotency-key"] for r in requests}) == 1
    assert row(config)["attempts"] == 3
    assert asyncio.run(process_once(config, send=True, now=2000))["status"] == "idle"


def test_lease_allows_only_one_worker_and_recovers_after_expiration(tmp_path):
    config = queue(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: _claim(config, send=False, now=1000), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    assert _claim(config, send=False, now=1089) is None
    assert _claim(config, send=False, now=1091) is not None


def test_enrollment_change_blocks_old_queued_work(tmp_path):
    config = queue(tmp_path)
    config.nessie_customer_id = "another-customer"
    assert asyncio.run(process_once(config, send=True, now=1000))["status"] == "blocked"
    assert row(config)["attempts"] == 0


@pytest.mark.parametrize(
    "text",
    [
        "/help",
        "/status",
        "pay someone",
        "/summary",
        "/summary 2026-12-01 2026-11-01",
        "/accounts extra",
    ],
)
def test_nonfinancial_or_invalid_commands_never_load_data(tmp_path, text):
    async def forbidden(_):
        pytest.fail("No snapshot read expected")

    reply, source = asyncio.run(quick_reply(text, settings(tmp_path), snapshot_loader=forbidden))
    assert source is None and len(reply) <= 1000


def test_tampered_prepared_reply_is_blocked_without_transport(tmp_path):
    config = queue(tmp_path, "/help")
    asyncio.run(process_once(config, now=1000))
    with closing(sqlite3.connect(config.photon_inbox_path)) as db, db:
        db.execute("UPDATE quick_replies SET text='Changed'")
    assert asyncio.run(process_once(config, send=True, now=1001))["status"] == "blocked"
    assert row(config)["attempts"] == 0
