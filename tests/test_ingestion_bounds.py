"""Independent source-boundary expectations using tiny synthetic input only."""

import asyncio
import copy
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from spending_api import nessie
from spending_api.agent_tools import SpendingTools
from spending_api.config import Settings
from spending_api.models import DateRange

REAL_ASYNC_CLIENT = httpx.AsyncClient
LIMIT_ERROR = "Spending source exceeds the demo resource limit; no partial totals returned"
INVALID_ERROR = "Spending source returned invalid data; no partial totals returned"
ENCODING_ERROR = "Spending source uses unsupported content encoding"
DEADLINE_ERROR = "Spending source read deadline exceeded"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def source_payload():
    return {
        "customer_id": "synthetic-customer",
        "accounts": [{
            "id": "synthetic-account", "customer_id": "synthetic-customer",
            "nickname": "Synthetic", "type": "Checking", "balance_cents": 9000,
        }],
        "merchants": [{"id": "synthetic-merchant", "name": "Synthetic", "category": "dining"}],
        "transactions": [{
            "id": "synthetic-purchase", "payer_id": "synthetic-account",
            "merchant_id": "synthetic-merchant", "kind": "purchase", "status": "completed",
            "amount_cents": 1250, "created_at": "2026-11-01T00:00:00Z",
        }],
    }


def live_settings():
    return Settings(
        _env_file=None, data_source="nessie", nessie_base_url="https://synthetic.invalid",
        nessie_customer_id="synthetic-customer", nessie_api_key="synthetic-provider-key",
        app_api_token="synthetic-app-token",
    )


def fixture_settings(tmp_path, content):
    path = tmp_path / "synthetic.json"
    path.write_bytes(content)
    return Settings(_env_file=None, data_source="fixture", fixture_path=path)


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self):
        self.closed = True


class WaitingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.closed = False

    async def __aiter__(self):
        self.entered.set()
        try:
            await self.release.wait()
            yield b'{"items":[]}'
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def aclose(self):
        self.closed = True


def install_transport(monkeypatch, handler):
    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(nessie.httpx, "AsyncClient", client)


def read_pages(handler):
    async def read():
        async with REAL_ASYNC_CLIENT(
            base_url="https://synthetic.invalid", transport=httpx.MockTransport(handler),
        ) as client:
            return await nessie.collect_pages(client, "/transactions")

    return asyncio.run(read())


@pytest.mark.parametrize("page", [
    [], None, {}, {"items": None}, {"items": False}, {"items": 0},
    {"items": "records"}, {"items": {}}, {"items": [None]}, {"items": ["record"]},
])
def test_malformed_page_never_becomes_an_empty_success(page):
    with pytest.raises(nessie.DataSourceError) as error:
        read_pages(lambda _: httpx.Response(200, json=page))
    assert str(error.value) == INVALID_ERROR


@pytest.mark.parametrize("body", [
    b"{broken", b'{"items":[],"items":[{"id":"hidden"}]}',
    b'{"items":[],"metadata":NaN}', b'{"items":[],"metadata":Infinity}',
    b'{"items":[],"metadata":1e999}',
    b'{"items":[],"metadata":"\xff"}',
])
def test_malformed_json_is_sanitized_without_parser_details(body):
    with pytest.raises(nessie.DataSourceError) as error:
        read_pages(lambda _: httpx.Response(200, content=body))
    assert str(error.value) == INVALID_ERROR


def test_bad_later_page_invalidates_earlier_financial_records():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"items": [{"id": "earlier"}], "next_cursor": "next"})
        return httpx.Response(200, json={"next_cursor": None})

    with pytest.raises(nessie.DataSourceError) as error:
        read_pages(handler)
    assert str(error.value) == INVALID_ERROR
    assert len(calls) == 2


@pytest.mark.parametrize("terminal", [{}, {"next_cursor": None}, {"next_cursor": ""}])
def test_terminal_cursor_forms_and_buffered_mock_responses_remain_supported(terminal):
    assert read_pages(lambda _: httpx.Response(200, json={"items": [], **terminal})) == []


@pytest.mark.parametrize("cursor", [False, True, 0, 1, [], {}])
def test_cursor_types_are_not_coerced_or_mistaken_for_completion(cursor):
    with pytest.raises(nessie.DataSourceError) as error:
        read_pages(lambda _: httpx.Response(200, json={"items": [], "next_cursor": cursor}))
    assert str(error.value) == INVALID_ERROR


def test_opaque_cursor_uses_utf8_limit_and_empty_page_still_continues(monkeypatch):
    cursor = " é "
    monkeypatch.setattr(nessie, "MAX_CURSOR_BYTES", len(cursor.encode("utf-8")))
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"items": [], "next_cursor": cursor})
        assert request.url.params["cursor"] == cursor
        return httpx.Response(200, json={"items": [{"id": "last"}]})

    assert read_pages(handler) == [{"id": "last"}]
    assert len(calls) == 2
    monkeypatch.setattr(nessie, "MAX_CURSOR_BYTES", len(cursor.encode("utf-8")) - 1)
    with pytest.raises(nessie.DataSourceError) as error:
        read_pages(lambda _: httpx.Response(200, json={"items": [], "next_cursor": cursor}))
    assert str(error.value) == LIMIT_ERROR


def test_repeated_cursor_fails_without_requesting_a_third_page():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [], "next_cursor": "same"})

    with pytest.raises(nessie.DataSourceError):
        read_pages(handler)
    assert len(calls) == 2


@pytest.mark.parametrize("continues", [False, True])
def test_last_permitted_page_must_be_terminal(monkeypatch, continues):
    monkeypatch.setattr(nessie, "MAX_COLLECTION_PAGES", 2)
    calls = []

    def handler(request):
        calls.append(request)
        cursor = f"next-{len(calls)}" if len(calls) == 1 or continues else None
        return httpx.Response(200, json={"items": [{"id": str(len(calls))}], "next_cursor": cursor})

    if continues:
        with pytest.raises(nessie.DataSourceError) as error:
            read_pages(handler)
        assert str(error.value) == LIMIT_ERROR
    else:
        assert read_pages(handler) == [{"id": "1"}, {"id": "2"}]
    assert len(calls) == 2


def test_overlong_page_is_rejected_in_full(monkeypatch):
    monkeypatch.setattr(nessie, "MAX_PAGE_ITEMS", 2)
    rows = [{"id": str(number)} for number in range(3)]
    with pytest.raises(nessie.DataSourceError) as error:
        read_pages(lambda _: httpx.Response(200, json={"items": rows}))
    assert str(error.value) == LIMIT_ERROR


def test_duplicate_overlap_consumes_raw_collection_record_budget(monkeypatch):
    monkeypatch.setattr(nessie, "MAX_COLLECTION_ROWS", 1)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            "items": [{"id": "same"}], "next_cursor": "next" if len(calls) == 1 else None,
        })

    with pytest.raises(nessie.DataSourceError) as error:
        read_pages(handler)
    assert str(error.value) == LIMIT_ERROR
    assert len(calls) == 2


@pytest.mark.parametrize("content_length", [None, "1"])
@pytest.mark.parametrize("extra", [False, True])
def test_streamed_page_byte_limit_ignores_content_length(monkeypatch, content_length, extra):
    body = encoded({"items": [{"id": "synthetic"}]})
    monkeypatch.setattr(nessie, "MAX_PAGE_BYTES", len(body))
    chunks = [body[:7], body[7:]] + ([b" ", b"never-read"] if extra else [])
    stream = CountingStream(chunks)
    headers = {} if content_length is None else {"Content-Length": content_length}

    def handler(request):
        return httpx.Response(200, headers=headers, stream=stream)

    if extra:
        with pytest.raises(nessie.DataSourceError) as error:
            read_pages(handler)
        assert str(error.value) == LIMIT_ERROR
        assert stream.yielded == 3
    else:
        assert read_pages(handler) == [{"id": "synthetic"}]
        assert stream.yielded == 2
    assert stream.closed


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "zstd", "gzip, identity"])
def test_unsupported_encoding_is_rejected_before_body_read(encoding):
    stream = CountingStream([b"never-read"])
    with pytest.raises(nessie.DataSourceError) as error:
        read_pages(
            lambda _: httpx.Response(200, headers={"Content-Encoding": encoding}, stream=stream)
        )
    assert str(error.value) == ENCODING_ERROR
    assert stream.yielded == 0 and stream.closed


@pytest.mark.parametrize("extra_capacity", [0, -1])
def test_shared_snapshot_bytes_cover_all_collections(monkeypatch, extra_capacity):
    raw = source_payload()
    bodies = {
        name: encoded({"items": raw[name]})
        for name in ("accounts", "merchants", "transactions")
    }
    monkeypatch.setattr(
        nessie, "MAX_SNAPSHOT_BYTES", sum(map(len, bodies.values())) + extra_capacity
    )
    streams = []

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["authorization"] == "Bearer synthetic-provider-key"
        stream = CountingStream([bodies[request.url.path.rsplit("/", 1)[-1]]])
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    install_transport(monkeypatch, handler)
    if extra_capacity < 0:
        monkeypatch.setattr(
            nessie, "normalize", lambda *args: pytest.fail("oversized source was normalized")
        )
        with pytest.raises(nessie.DataSourceError) as error:
            asyncio.run(nessie.load_snapshot(live_settings()))
        assert str(error.value) == LIMIT_ERROR
    else:
        snapshot = asyncio.run(nessie.load_snapshot(live_settings()))
        assert SpendingTools(snapshot).summarize_spending(DateRange()).net_spending_cents == 1250
    assert streams and all(stream.closed for stream in streams)


def test_foreign_rows_consume_shared_budget_before_scoping(monkeypatch):
    raw = source_payload()
    raw["transactions"].append({
        **raw["transactions"][0], "id": "foreign", "payer_id": "unowned-account",
    })
    monkeypatch.setattr(nessie, "MAX_SNAPSHOT_ROWS", 3)
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={
        "items": raw[request.url.path.rsplit("/", 1)[-1]],
    }))
    monkeypatch.setattr(
        nessie, "normalize", lambda *args: pytest.fail("oversized source was normalized")
    )
    with pytest.raises(nessie.DataSourceError) as error:
        asyncio.run(nessie.load_snapshot(live_settings()))
    assert str(error.value) == LIMIT_ERROR


def test_exact_fixture_byte_and_record_limits_preserve_financial_totals(tmp_path, monkeypatch):
    body = encoded(source_payload())
    monkeypatch.setattr(nessie, "MAX_SNAPSHOT_BYTES", len(body))
    monkeypatch.setattr(nessie, "MAX_SNAPSHOT_ROWS", 3)
    monkeypatch.setattr(nessie, "MAX_COLLECTION_ROWS", 1)
    settings = fixture_settings(tmp_path, body)
    snapshot = asyncio.run(nessie.load_snapshot(settings))
    assert len(snapshot.accounts) == len(snapshot.transactions) == 1
    assert SpendingTools(snapshot).summarize_spending(DateRange()).net_spending_cents == 1250
    settings.fixture_path.write_bytes(body + b" ")
    monkeypatch.setattr(
        nessie, "normalize", lambda *args: pytest.fail("oversized fixture was normalized")
    )
    with pytest.raises(nessie.DataSourceError) as error:
        asyncio.run(nessie.load_snapshot(settings))
    assert str(error.value) == LIMIT_ERROR


@pytest.mark.parametrize("body", [
    b"{private-parser-fragment", b"\xff", b"[]", b"null",
    b'{"customer_id":"synthetic-customer","accounts":[],"merchants":[]}',
    b'{"customer_id":"synthetic-customer","accounts":[],"merchants":[],"transactions":null}',
    b'{"customer_id":"synthetic-customer","accounts":[],"merchants":[],"transactions":[],"x":NaN}',
    b'{"customer_id":"synthetic-customer","accounts":[],"merchants":[],"transactions":[],"x":1e999}',
    b'{"customer_id":"synthetic-customer","accounts":[],"accounts":[],"merchants":[],"transactions":[]}',
])
def test_invalid_fixture_is_a_fixed_source_error(tmp_path, body):
    with pytest.raises(nessie.DataSourceError) as error:
        asyncio.run(nessie.load_snapshot(fixture_settings(tmp_path, body)))
    assert str(error.value) == INVALID_ERROR


@pytest.mark.parametrize("limit_name,limit", [("MAX_COLLECTION_ROWS", 1), ("MAX_SNAPSHOT_ROWS", 3)])
def test_fixture_raw_record_limit_applies_before_deduplication(
    tmp_path, monkeypatch, limit_name, limit
):
    raw = source_payload()
    raw["transactions"].append(copy.deepcopy(raw["transactions"][0]))
    monkeypatch.setattr(nessie, limit_name, limit)
    with pytest.raises(nessie.DataSourceError) as error:
        asyncio.run(nessie.load_snapshot(fixture_settings(tmp_path, encoded(raw))))
    assert str(error.value) == LIMIT_ERROR


@pytest.mark.parametrize("failure", ["malformed_page", "http_status"])
def test_one_collection_failure_cancels_and_drains_sibling_streams(monkeypatch, failure):
    async def scenario():
        siblings = {name: WaitingStream() for name in ("merchants", "transactions")}

        async def handler(request):
            name = request.url.path.rsplit("/", 1)[-1]
            if name == "accounts":
                await asyncio.gather(*(stream.entered.wait() for stream in siblings.values()))
                return httpx.Response(
                    200 if failure == "malformed_page" else 502, json={"items": None}
                )
            return httpx.Response(200, stream=siblings[name])

        install_transport(monkeypatch, handler)
        monkeypatch.setattr(
            nessie, "normalize", lambda *args: pytest.fail("failed load was normalized")
        )
        expected_error = (
            nessie.DataSourceError if failure == "malformed_page" else httpx.HTTPStatusError
        )
        with pytest.raises(expected_error) as error:
            await asyncio.wait_for(nessie.load_snapshot(live_settings()), 1)
        if failure == "malformed_page":
            assert str(error.value) == INVALID_ERROR
        assert all(stream.cancelled and stream.closed for stream in siblings.values())

    asyncio.run(scenario())


@pytest.mark.parametrize("termination", ["caller_cancel", "deadline"])
def test_cancelled_load_drains_all_streams_before_returning(monkeypatch, termination):
    async def scenario():
        streams = {name: WaitingStream() for name in ("accounts", "merchants", "transactions")}
        install_transport(monkeypatch, lambda request: httpx.Response(
            200, stream=streams[request.url.path.rsplit("/", 1)[-1]],
        ))
        if termination == "deadline":
            monkeypatch.setattr(nessie, "SNAPSHOT_TIMEOUT_SECONDS", 0.05)
        task = asyncio.create_task(nessie.load_snapshot(live_settings()))
        await asyncio.wait_for(
            asyncio.gather(*(stream.entered.wait() for stream in streams.values())), 1
        )
        if termination == "caller_cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(nessie.DataSourceError) as error:
                await asyncio.wait_for(task, 1)
            assert str(error.value) == DEADLINE_ERROR
        assert all(stream.cancelled and stream.closed for stream in streams.values())

    asyncio.run(scenario())


def test_synchronous_phase_cannot_return_success_after_source_deadline(tmp_path, monkeypatch):
    original = nessie.normalize
    monkeypatch.setattr(nessie, "SNAPSHOT_TIMEOUT_SECONDS", 0.01)

    def slow_normalize(*args):
        time.sleep(0.02)
        return original(*args)

    monkeypatch.setattr(nessie, "normalize", slow_normalize)
    with pytest.raises(nessie.DataSourceError) as error:
        asyncio.run(nessie.load_snapshot(fixture_settings(tmp_path, encoded(source_payload()))))
    assert str(error.value) == DEADLINE_ERROR


def test_fixture_read_remains_bounded_when_file_size_hint_is_stale(tmp_path, monkeypatch):
    limit = 16
    settings = fixture_settings(tmp_path, b" " * 100)
    monkeypatch.setattr(nessie, "MAX_SNAPSHOT_BYTES", limit)
    original_open, original_fstat = Path.open, os.fstat
    descriptors, requested, returned = set(), [], []

    class GuardedFile:
        def __init__(self, file):
            self.file = file
            descriptors.add(file.fileno())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def fileno(self):
            return self.file.fileno()

        def read(self, size=-1):
            assert 0 <= size <= limit + 1, "fixture boundary attempted an unbounded read"
            requested.append(size)
            result = self.file.read(size)
            returned.append(len(result))
            return result

    def open_guarded(path, *args, **kwargs):
        file = original_open(path, *args, **kwargs)
        if path == settings.fixture_path:
            assert args[0] == "rb"
            return GuardedFile(file)
        return file

    def stale_size(descriptor):
        return (
            SimpleNamespace(st_size=0) if descriptor in descriptors else original_fstat(descriptor)
        )

    monkeypatch.setattr(Path, "open", open_guarded)
    monkeypatch.setattr(os, "fstat", stale_size)
    monkeypatch.setattr(
        nessie, "normalize", lambda *args: pytest.fail("oversized fixture was normalized")
    )
    with pytest.raises(nessie.DataSourceError) as error:
        asyncio.run(nessie.load_snapshot(settings))
    assert str(error.value) == LIMIT_ERROR
    assert requested and sum(returned) <= limit + 1


@pytest.mark.parametrize("timestamp", [None, False, 42, [], {}, "private-invalid-created-at"])
@pytest.mark.parametrize("source", ["fixture", "nessie"])
def test_invalid_timestamp_type_or_value_has_no_payload_in_error(
    tmp_path, monkeypatch, timestamp, source
):
    raw = source_payload()
    raw["transactions"][0]["created_at"] = timestamp
    if source == "fixture":
        settings = fixture_settings(tmp_path, encoded(raw))
    else:
        settings = live_settings()
        install_transport(monkeypatch, lambda request: httpx.Response(200, json={
            "items": raw[request.url.path.rsplit("/", 1)[-1]],
        }))
    with pytest.raises(nessie.DataSourceError) as error:
        asyncio.run(nessie.load_snapshot(settings))
    assert str(error.value) == INVALID_ERROR


@pytest.mark.parametrize(
    "route", ["/api/v1/dashboard", "/api/v1/spending/summary", "/api/v1/transactions"]
)
def test_malformed_source_has_sanitized_api_failure_instead_of_partial_totals(tmp_path, route):
    from fastapi.testclient import TestClient

    from spending_api.main import create_app

    settings = fixture_settings(tmp_path, b'{"private-parser-fragment":')
    with TestClient(create_app(settings)) as client:
        response = client.get(route)
    assert response.status_code == 503
    assert response.json() == {"detail": INVALID_ERROR}
