"""Validator-owned admission/lifetime checks; native work uses bounded event gates."""

import asyncio
import threading
from datetime import UTC, datetime

import httpx
import pytest

from spending_api.comparison import compare_spending as calculate
from spending_api.config import Settings
from spending_api.main import create_app
from spending_api.models import Account, Snapshot, Transaction

AUTH = {"Authorization": "Bearer lifecycle-fixture-token"}
PERIODS = {
    "baseline": {"start": "2026-11-01", "end": "2026-11-01"},
    "current": {"start": "2026-11-02", "end": "2026-11-02"},
}


def fixture():
    return Snapshot(
        source="fixture",
        accounts=[
            Account(id="a", customer_id="c", nickname="Synthetic", type="Checking", balance_cents=0)
        ],
        transactions=[
            Transaction(
                id="t",
                account_id="a",
                merchant="Fixture",
                merchant_id="m",
                category="food",
                kind="purchase",
                status="completed",
                amount_cents=100,
                occurred_at=datetime(2026, 11, 2, tzinfo=UTC),
            )
        ],
    )


def application():
    return create_app(
        Settings(_env_file=None, data_source="fixture", app_api_token="lifecycle-fixture-token")
    )


async def wait_event(event):
    async def poll():
        while not event.is_set():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), 1)


@pytest.mark.parametrize("termination", ["deadline", "caller_cancel"])
@pytest.mark.parametrize("late_error", [False, True])
def test_native_comparison_keeps_admission_until_actual_thread_exit(
    monkeypatch, termination, late_error
):
    async def scenario():
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        snapshot_reads, native_calls, loop_errors = [], [], []
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
        actual_timeout = asyncio.timeout
        monkeypatch.setattr(
            "spending_api.main.asyncio.timeout", lambda _seconds: actual_timeout(0.03)
        )

        async def load(_settings):
            snapshot_reads.append(True)
            return fixture()

        def gated_worker(tools, periods):
            native_calls.append(True)
            first_call = len(native_calls) == 1
            if first_call:
                entered.set()
            try:
                if first_call:
                    if not release.wait(2):
                        raise RuntimeError("Synthetic test gate was not released")
                    if late_error:
                        raise ValueError("Synthetic late worker error")
                return calculate(tools, periods)
            finally:
                if first_call:
                    finished.set()

        monkeypatch.setattr("spending_api.main.load_snapshot", load)
        monkeypatch.setattr("spending_api.main.compare_spending", gated_worker)
        first = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application()), base_url="http://fixture"
            ) as client:
                first = asyncio.create_task(
                    client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
                )
                await wait_event(entered)
                if termination == "caller_cancel":
                    first.cancel()
                    await asyncio.sleep(0.01)
                else:
                    await asyncio.sleep(0.06)
                assert not finished.is_set(), "The probe must observe genuinely active native work"
                second = await client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
                assert second.status_code == 429, (
                    "An abandoned awaiter must not reopen native-work admission"
                )
                assert len(snapshot_reads) == len(native_calls) == 1
                release.set()
                await wait_event(finished)
                first_result = (await asyncio.gather(first, return_exceptions=True))[0]
                if termination == "deadline":
                    assert first_result.status_code == 504
                else:
                    assert isinstance(first_result, asyncio.CancelledError)
                await asyncio.sleep(0.01)
                third = await client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
                assert third.status_code == 200
                assert len(snapshot_reads) == len(native_calls) == 2
                assert loop_errors == [], "Late worker exceptions must be retrieved"
        finally:
            release.set()
            if first is not None:
                await asyncio.gather(first, return_exceptions=True)
            if entered.is_set():
                await wait_event(finished)
            await asyncio.sleep(0.01)
            loop.set_exception_handler(old_handler)

    asyncio.run(scenario())


def test_cancelled_source_load_releases_admission_without_starting_native_work(monkeypatch):
    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        loads, native = [], []

        async def load(_settings):
            loads.append(True)
            if len(loads) == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return fixture()

        def worker(tools, periods):
            native.append(True)
            return calculate(tools, periods)

        monkeypatch.setattr("spending_api.main.load_snapshot", load)
        monkeypatch.setattr("spending_api.main.compare_spending", worker)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application()), base_url="http://fixture"
        ) as client:
            first = asyncio.create_task(
                client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
            )
            await asyncio.wait_for(entered.wait(), 1)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            assert cancelled.is_set() and native == []
            response = await client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
            assert response.status_code == 200 and len(loads) == 2 and len(native) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("worker_error", [False, True])
def test_shutdown_stops_admission_and_drains_native_work(monkeypatch, worker_error):
    async def scenario():
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        async def load(_settings):
            return fixture()

        def worker(tools, periods):
            entered.set()
            try:
                if not release.wait(2):
                    raise RuntimeError("Synthetic test gate was not released")
                if worker_error:
                    raise ValueError("Synthetic shutdown worker error")
                return calculate(tools, periods)
            finally:
                finished.set()

        monkeypatch.setattr("spending_api.main.load_snapshot", load)
        monkeypatch.setattr("spending_api.main.compare_spending", worker)
        app = application()
        lifecycle = app.router.lifespan_context(app)
        await lifecycle.__aenter__()
        first = shutdown = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://fixture"
            ) as client:
                first = asyncio.create_task(
                    client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
                )
                await wait_event(entered)
                shutdown = asyncio.create_task(lifecycle.__aexit__(None, None, None))
                await asyncio.sleep(0.01)
                assert not shutdown.done() and not finished.is_set()
                rejected = await client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
                assert rejected.status_code == 503
                release.set()
                response = await first
                assert response.status_code == (503 if worker_error else 200)
                await asyncio.wait_for(shutdown, 1)
                assert finished.is_set()
                assert (
                    await client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
                ).status_code == 503
        finally:
            release.set()
            if first is not None:
                await asyncio.gather(first, return_exceptions=True)
            if shutdown is not None:
                await asyncio.wait_for(asyncio.gather(shutdown, return_exceptions=True), 1)
            else:
                await lifecycle.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_source_load_finishing_during_shutdown_cannot_start_native_work(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        native = []

        async def load(_settings):
            entered.set()
            await release.wait()
            return fixture()

        def forbidden_worker(*_args):
            native.append(True)
            raise AssertionError("Shutdown must not start native work")

        monkeypatch.setattr("spending_api.main.load_snapshot", load)
        monkeypatch.setattr("spending_api.main.compare_spending", forbidden_worker)
        app = application()
        lifecycle = app.router.lifespan_context(app)
        await lifecycle.__aenter__()
        first = shutdown = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://fixture"
            ) as client:
                first = asyncio.create_task(
                    client.post("/api/v1/spending/compare", headers=AUTH, json=PERIODS)
                )
                await asyncio.wait_for(entered.wait(), 1)
                shutdown = asyncio.create_task(lifecycle.__aexit__(None, None, None))
                await asyncio.sleep(0.01)
                assert not shutdown.done()
                release.set()
                assert (await first).status_code == 503
                await asyncio.wait_for(shutdown, 1)
                assert native == []
        finally:
            release.set()
            if first is not None:
                await asyncio.gather(first, return_exceptions=True)
            if shutdown is not None:
                await asyncio.wait_for(asyncio.gather(shutdown, return_exceptions=True), 1)
            else:
                await lifecycle.__aexit__(None, None, None)

    asyncio.run(scenario())
