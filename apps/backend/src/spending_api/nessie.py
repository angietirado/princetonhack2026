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
    accounts = [
        Account.model_validate(a) for a in raw["accounts"] if a["customer_id"] == customer_id
    ]
    account_ids = {a.id for a in accounts}
    merchants = {m["id"]: m for m in raw["merchants"]}
    all_transactions = {t["id"]: t for t in raw["transactions"]}
    transactions = []
    for tx in all_transactions.values():
        owner = next(
            (tx.get(field) for field in ("payer_id", "payee_id") if tx.get(field) in account_ids),
            None,
        )
        if owner is None:
            continue
        merchant_id = tx.get("merchant_id")
        if not merchant_id and tx.get("parent_transaction_id"):
            parent = all_transactions.get(tx["parent_transaction_id"], {})
            merchant_id = parent.get("merchant_id")
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
