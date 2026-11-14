"""Read-only Nessie v2 boundary. No financial writes belong in the app."""

import asyncio
import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from spending_api.config import Settings
from spending_api.models import Account, Snapshot, Transaction

MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_PAGE_BYTES = 1024 * 1024
MAX_PAGE_ITEMS = 100
MAX_COLLECTION_PAGES = 100
MAX_COLLECTION_ROWS = 10_000
MAX_SNAPSHOT_ROWS = 30_000
MAX_CURSOR_BYTES = 2048
SNAPSHOT_TIMEOUT_SECONDS = 30.0

RESOURCE_LIMIT_ERROR = "Spending source exceeds the demo resource limit; no partial totals returned"
INVALID_DATA_ERROR = "Spending source returned invalid data; no partial totals returned"
CONTENT_ENCODING_ERROR = "Spending source uses unsupported content encoding"
READ_DEADLINE_ERROR = "Spending source read deadline exceeded"


class DataSourceError(Exception):
    pass


@dataclass
class LoadBudget:
    """One load's budget, including foreign records and duplicate page overlaps."""

    bytes_used: int = 0
    rows_used: int = 0

    def add_bytes(self, size: int) -> None:
        if self.bytes_used + size > MAX_SNAPSHOT_BYTES:
            raise DataSourceError(RESOURCE_LIMIT_ERROR)
        self.bytes_used += size

    def add_rows(self, count: int) -> None:
        if self.rows_used + count > MAX_SNAPSHOT_ROWS:
            raise DataSourceError(RESOURCE_LIMIT_ERROR)
        self.rows_used += count


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def _finite_json_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite JSON number")
    return result


def _decode_source(body: bytes | bytearray) -> dict:
    try:
        result = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (ValueError, TypeError, RecursionError) as exc:
        raise DataSourceError(INVALID_DATA_ERROR) from exc
    if not isinstance(result, dict):
        raise DataSourceError(INVALID_DATA_ERROR)
    return result


def _read_fixture(path: Path, budget: LoadBudget) -> dict:
    try:
        with path.open("rb") as source:
            # The hint avoids an unnecessary read; the bounded read also covers
            # a file that grows after fstat or reports an unhelpful size.
            if os.fstat(source.fileno()).st_size > MAX_SNAPSHOT_BYTES:
                raise DataSourceError(RESOURCE_LIMIT_ERROR)
            body = source.read(MAX_SNAPSHOT_BYTES + 1)
    except FileNotFoundError as exc:
        raise DataSourceError("Generate .data/demo.json with generate-data first") from exc
    except OSError as exc:
        raise DataSourceError(INVALID_DATA_ERROR) from exc
    budget.add_bytes(len(body))
    raw = _decode_source(body)
    for name in ("accounts", "merchants", "transactions"):
        rows = raw.get(name)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DataSourceError(INVALID_DATA_ERROR)
        if len(rows) > MAX_COLLECTION_ROWS:
            raise DataSourceError(RESOURCE_LIMIT_ERROR)
        budget.add_rows(len(rows))
    return raw


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataSourceError("Nessie returned invalid or conflicting record identities")
    return value


def _reference(value: object) -> str | None:
    return None if value is None else _identifier(value)


def _unique_records(rows: object) -> dict[str, dict]:
    """Deduplicate identical page overlaps, rejecting conflicts before scope filtering.

    Compare raw JSON with types intact: Python equality would consider True, 1
    and 1.0 equal and could hide an invalid financial value behind a duplicate.
    """
    if not isinstance(rows, list):
        raise DataSourceError("Nessie returned invalid or conflicting record identities")
    records, fingerprints = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            raise DataSourceError("Nessie returned invalid or conflicting record identities")
        identifier = _identifier(row.get("id"))
        try:
            fingerprint = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise DataSourceError(
                "Nessie returned invalid or conflicting record identities"
            ) from exc
        if identifier in fingerprints and fingerprints[identifier] != fingerprint:
            raise DataSourceError("Nessie returned invalid or conflicting record identities")
        fingerprints[identifier] = fingerprint
        records.setdefault(identifier, row)
    return records


async def collect_pages(
    client: httpx.AsyncClient, path: str, *, budget: LoadBudget | None = None
) -> list[dict]:
    budget = budget if budget is not None else LoadBudget()
    rows: list[dict] = []
    cursor = None
    seen: set[str] = set()
    for _ in range(MAX_COLLECTION_PAGES):
        params = {"limit": MAX_PAGE_ITEMS}
        if cursor:
            params["cursor"] = cursor
        async with client.stream(
            "GET", path, params=params, headers={"Accept-Encoding": "identity"}
        ) as response:
            response.raise_for_status()
            # HTTPX decodes compressed chunks before yielding aiter_bytes.
            # Reject compression before that step so expansion cannot outrun
            # our byte checks. The identity path also supports buffered mocks.
            if response.headers.get("content-encoding", "").strip().lower() not in (
                "",
                "identity",
            ):
                raise DataSourceError(CONTENT_ENCODING_ERROR)
            length = response.headers.get("content-length")
            if length is not None:
                length = length.strip()
                if not length.isascii() or not length.isdigit() or len(length) > 20:
                    raise DataSourceError(INVALID_DATA_ERROR)
                declared = int(length)
                if declared > MAX_PAGE_BYTES or budget.bytes_used + declared > MAX_SNAPSHOT_BYTES:
                    raise DataSourceError(RESOURCE_LIMIT_ERROR)
            body = bytearray()
            chunk_size = min(
                64 * 1024,
                MAX_PAGE_BYTES + 1,
                MAX_SNAPSHOT_BYTES - budget.bytes_used + 1,
            )
            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                if len(body) + len(chunk) > MAX_PAGE_BYTES:
                    raise DataSourceError(RESOURCE_LIMIT_ERROR)
                budget.add_bytes(len(chunk))
                body.extend(chunk)
        page = _decode_source(body)
        items = page.get("items")
        if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
            raise DataSourceError(INVALID_DATA_ERROR)
        if len(items) > MAX_PAGE_ITEMS or len(rows) + len(items) > MAX_COLLECTION_ROWS:
            raise DataSourceError(RESOURCE_LIMIT_ERROR)
        budget.add_rows(len(items))
        rows.extend(items)
        cursor = page.get("next_cursor")
        if cursor is None or cursor == "":
            return rows
        if not isinstance(cursor, str):
            raise DataSourceError(INVALID_DATA_ERROR)
        try:
            cursor_size = len(cursor.encode("utf-8"))
        except UnicodeError as exc:
            raise DataSourceError(INVALID_DATA_ERROR) from exc
        if cursor_size > MAX_CURSOR_BYTES:
            raise DataSourceError(RESOURCE_LIMIT_ERROR)
        if cursor in seen:
            raise DataSourceError("Nessie returned a repeated pagination cursor")
        seen.add(cursor)
    raise DataSourceError(RESOURCE_LIMIT_ERROR)


def normalize(raw: dict, source: str, customer_id: str) -> Snapshot:
    _identifier(customer_id)
    all_accounts = _unique_records(raw["accounts"])
    merchants = _unique_records(raw["merchants"])
    all_transactions = _unique_records(raw["transactions"])
    accounts = [
        Account.model_validate(a)
        for a in all_accounts.values()
        if _identifier(a.get("customer_id")) == customer_id
    ]
    account_ids = {a.id for a in accounts}
    transactions = []
    for tx in all_transactions.values():
        endpoints = [_reference(tx.get(field)) for field in ("payer_id", "payee_id")]
        owner = next(
            (endpoint for endpoint in endpoints if endpoint in account_ids),
            None,
        )
        if owner is None:
            continue
        merchant_id = _reference(tx.get("merchant_id"))
        if merchant_id is None and tx.get("kind") == "refund":
            parent_id = _reference(tx.get("parent_transaction_id"))
            parent = all_transactions.get(parent_id, {})
            # A purchase belongs to its payer. An owned payee on a foreign
            # purchase must not make that purchase a valid refund parent.
            if (
                parent.get("kind") == "purchase"
                and parent.get("status") == "completed"
                and parent.get("payer_id") == owner
            ):
                merchant_id = _reference(parent.get("merchant_id"))
        merchant = merchants.get(merchant_id, {})
        created_at = tx.get("created_at")
        if not isinstance(created_at, str):
            raise DataSourceError(INVALID_DATA_ERROR)
        occurred = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            raise DataSourceError("Nessie transaction timestamp must include a timezone")
        transactions.append(
            Transaction(
                id=tx["id"],
                account_id=owner,
                kind=tx["kind"],
                status=tx["status"],
                amount_cents=tx["amount_cents"],
                occurred_at=occurred.astimezone(UTC),
                merchant=merchant.get("name", "Unknown merchant"),
                merchant_id=merchant_id,
                category=merchant.get("category") or "uncategorized",
                description=tx.get("description", ""),
            )
        )
    transactions.sort(key=lambda tx: (tx.occurred_at, tx.id), reverse=True)
    return Snapshot(source=source, accounts=accounts, transactions=transactions)


async def load_snapshot(settings: Settings) -> Snapshot:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SNAPSHOT_TIMEOUT_SECONDS

    def check_deadline() -> None:
        # asyncio timeout cannot interrupt synchronous file I/O or parsing.
        # Reject late success after those bounded phases as well.
        if loop.time() >= deadline:
            raise DataSourceError(READ_DEADLINE_ERROR)

    try:
        async with asyncio.timeout(SNAPSHOT_TIMEOUT_SECONDS):
            budget = LoadBudget()
            if settings.data_source == "fixture":
                raw = _read_fixture(settings.fixture_path, budget)
                check_deadline()
                customer_id = raw["customer_id"]
            else:
                async with httpx.AsyncClient(
                    base_url=settings.nessie_base_url.rstrip("/"),
                    timeout=15,
                    headers={
                        "Authorization": f"Bearer {settings.nessie_api_key.get_secret_value()}"
                    },
                ) as client:
                    paths = (
                        f"/customers/{quote(settings.nessie_customer_id, safe='')}/accounts",
                        "/merchants",
                        "/transactions",
                    )
                    jobs = [
                        asyncio.create_task(collect_pages(client, path, budget=budget))
                        for path in paths
                    ]
                    try:
                        accounts, merchants, transactions = await asyncio.gather(*jobs)
                    finally:
                        # A failed/cancelled collection must not outlive the
                        # client or keep fetching while another request starts.
                        for job in jobs:
                            if not job.done():
                                job.cancel()
                        await asyncio.gather(*jobs, return_exceptions=True)
                raw = {"accounts": accounts, "merchants": merchants, "transactions": transactions}
                customer_id = settings.nessie_customer_id
                check_deadline()
            snapshot = normalize(raw, settings.data_source, customer_id)
            check_deadline()
            return snapshot
    except TimeoutError as exc:
        raise DataSourceError(READ_DEADLINE_ERROR) from exc
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise DataSourceError(INVALID_DATA_ERROR) from exc
