"""Read-only Nessie v2 boundary. No financial writes belong in the app."""

import asyncio
import json
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from spending_api.config import Settings
from spending_api.models import Account, Snapshot, Transaction


class DataSourceError(Exception):
    pass


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


async def collect_pages(client: httpx.AsyncClient, path: str) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    seen: set[str] = set()
    for _ in range(100):
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(path, params=params)
        response.raise_for_status()
        page = response.json()
        rows.extend(page.get("items") or [])
        cursor = page.get("next_cursor")
        if not cursor:
            return rows
        if cursor in seen:
            raise DataSourceError("Nessie returned a repeated pagination cursor")
        seen.add(cursor)
    raise DataSourceError("Dataset exceeds the demo page limit; no partial totals returned")


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
        occurred = datetime.fromisoformat(tx["created_at"].replace("Z", "+00:00"))
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
    if settings.data_source == "fixture":
        try:
            raw = json.loads(settings.fixture_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DataSourceError("Generate .data/demo.json with generate-data first") from exc
        return normalize(raw, "fixture", raw["customer_id"])
    async with httpx.AsyncClient(
        base_url=settings.nessie_base_url.rstrip("/"),
        timeout=15,
        headers={"Authorization": f"Bearer {settings.nessie_api_key.get_secret_value()}"},
    ) as client:
        accounts, merchants, transactions = await asyncio.gather(
            collect_pages(
                client, f"/customers/{quote(settings.nessie_customer_id, safe='')}/accounts"
            ),
            collect_pages(client, "/merchants"),
            collect_pages(client, "/transactions"),
        )
    return normalize(
        {"accounts": accounts, "merchants": merchants, "transactions": transactions},
        "nessie",
        settings.nessie_customer_id,
    )
