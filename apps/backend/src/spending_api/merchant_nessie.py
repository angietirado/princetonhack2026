"""Read-only, merchant-scoped import of explicitly synthetic Nessie v2 records.

The public API exposes tenant-wide account/transaction lists. Unrelated records
are transient validation inputs only and never enter the resulting POS dataset.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime
from urllib.parse import quote

import httpx

from spending_api.merchant import Dataset, Order, Refund, _validate
from spending_api.merchant_import import encode_dataset

BASE_URL = "https://v2-prod-api.nessieisreal.com"
MAX_PAGES = 100
MAX_ROWS = 10_000
MAX_DATASET_ROWS = 5000
MAX_RESPONSE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 12_000_000
MAX_SECONDS = 60
WARNINGS = [
    "Nessie records are synthetic banking sandbox activity, not real POS sales or real customer behavior.",
    "Requested coverage is a filter over observed records; pagination does not prove complete or atomic sales history.",
    "Only completed purchases at the selected merchant and attributable completed refunds are included.",
    "Pending, declined, cancelled transactions, chargebacks, transfers, fees and other transaction kinds are excluded.",
    "Forced refunds without a parent and refunds with an unavailable parent cannot be attributed unless the merchant is explicit; unattributed refunds are excluded.",
    "Customer and transaction IDs are merchant-scoped pseudonyms; account details, balances, names and unrelated spending are not retained.",
    "Dates use transaction created_at converted to UTC, not settlement time or a verified POS visit timestamp.",
    "First observed purchase is limited by the requested coverage and is not necessarily a customer's first-ever purchase.",
]


class NessieImportError(ValueError):
    """A safe, fixed-text diagnostic that contains no provider payload or key."""


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise NessieImportError("Nessie returned an invalid record identity.")
    return value


def _optional_identity(value):
    return None if value in (None, "") else _identity(value)


def _cents(value):
    if type(value) is not int or not 1 <= value <= 9_999_999_999:
        raise NessieImportError("Nessie returned an invalid integer-cent amount.")
    return value


def _instant(value):
    if not isinstance(value, str) or len(value) > 40 or "T" not in value:
        raise NessieImportError("Nessie returned an invalid timestamp.")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError
        return moment.astimezone(UTC)
    except (ValueError, OverflowError):
        raise NessieImportError("Nessie timestamps must include a valid timezone.") from None


def _coverage(start, end):
    if type(start) is not date or type(end) is not date:
        raise NessieImportError("Coverage must use explicit calendar dates.")
    if not date(2000, 1, 1) <= start <= end <= date(2100, 12, 31) or (end - start).days > 730:
        raise NessieImportError("Choose ordered coverage of at most 731 days between 2000 and 2100.")


def _unique(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise NessieImportError("Nessie returned an invalid or oversized record list.")
    result, fingerprints = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            raise NessieImportError("Nessie returned an invalid record.")
        key = _identity(row.get("id"))
        try:
            fingerprint = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise NessieImportError("Nessie returned an invalid record.") from None
        if key in fingerprints and fingerprints[key] != fingerprint:
            raise NessieImportError("Conflicting duplicate Nessie records prevent a reliable import.")
        fingerprints[key] = fingerprint
        result.setdefault(key, row)
    return result


def _pseudonym(kind, merchant_id, identifier):
    digest = hashlib.sha256(json.dumps([kind, merchant_id, identifier], separators=(",", ":")).encode()).hexdigest()
    return f"nessie_{kind}_{digest}"


def normalize_merchant(raw: dict, *, merchant_id: str, coverage_start: date,
                       coverage_end: date) -> tuple[Dataset, dict]:
    """Convert a frozen documented response shape; perform no network or disk I/O."""
    merchant_id = _identity(merchant_id)
    _coverage(coverage_start, coverage_end)
    if not isinstance(raw, dict) or not isinstance(raw.get("merchant"), dict):
        raise NessieImportError("Nessie merchant metadata is missing.")
    if _identity(raw["merchant"].get("id")) != merchant_id:
        raise NessieImportError("Nessie returned a different merchant than requested.")
    accounts = _unique(raw.get("accounts"))
    transactions = _unique(raw.get("transactions"))
    selected = {}
    for key, tx in transactions.items():
        if tx.get("merchant_id") == merchant_id and tx.get("kind") not in {
            "purchase", "refund", "chargeback", "deposit", "withdrawal",
            "transfer", "bill_payment", "fee", "interest_credit",
        }:
            raise NessieImportError("A selected merchant record has an unknown transaction kind.")
        if tx.get("kind") != "purchase" or tx.get("merchant_id") != merchant_id:
            continue
        if tx.get("status") not in {"completed", "pending", "declined", "cancelled"}:
            raise NessieImportError("A selected purchase has an unknown status.")
        if tx["status"] != "completed":
            continue
        moment = _instant(tx.get("created_at"))
        if not coverage_start <= moment.date() <= coverage_end:
            continue
        account_id = _identity(tx.get("payer_id"))
        account = accounts.get(account_id)
        if account is None:
            raise NessieImportError("A selected purchase has no resolvable payer account.")
        customer = _identity(account.get("customer_id"))
        if _optional_identity(tx.get("payee_id")) is not None:
            raise NessieImportError("A selected purchase has unexpected transfer-account fields.")
        selected[key] = Order(
            _pseudonym("order", merchant_id, key), _pseudonym("customer", merchant_id, customer),
            moment.date(), _cents(tx.get("amount_cents")), "completed",
        )

    refunds, refunded = [], defaultdict(int)
    for key, tx in transactions.items():
        if tx.get("kind") != "refund":
            continue
        parent_id = _optional_identity(tx.get("parent_transaction_id"))
        parent = transactions.get(parent_id) if parent_id else None
        explicit_merchant = _optional_identity(tx.get("merchant_id"))
        parent_is_selected_merchant = bool(parent and parent.get("kind") == "purchase" and parent.get("merchant_id") == merchant_id)
        if not parent_is_selected_merchant and explicit_merchant != merchant_id:
            continue
        if tx.get("status") not in {"completed", "pending", "declined", "cancelled"}:
            raise NessieImportError("A selected merchant refund has an unknown status.")
        if tx["status"] != "completed":
            continue
        moment = _instant(tx.get("created_at"))
        if not coverage_start <= moment.date() <= coverage_end:
            continue
        if not parent_is_selected_merchant or parent.get("status") != "completed":
            raise NessieImportError("A selected merchant refund lacks a valid completed parent purchase.")
        if explicit_merchant not in {None, merchant_id}:
            raise NessieImportError("A refund merchant conflicts with its parent purchase.")
        if _identity(tx.get("payer_id")) != _identity(parent.get("payer_id")) or _optional_identity(tx.get("payee_id")) is not None:
            raise NessieImportError("A refund does not identify its original purchase account.")
        parent_moment = _instant(parent.get("created_at"))
        if moment < parent_moment:
            raise NessieImportError("A refund predates its original purchase.")
        if parent_moment.date() < coverage_start:
            raise NessieImportError("A refund's parent precedes requested coverage; extend the coverage start.")
        if parent_id not in selected:
            raise NessieImportError("A refund's parent is outside the selected purchase coverage.")
        amount = _cents(tx.get("amount_cents"))
        refunded[parent_id] += amount
        if refunded[parent_id] > selected[parent_id].amount_cents:
            raise NessieImportError("Selected refunds exceed their original purchase amount.")
        refunds.append(Refund(_pseudonym("refund", merchant_id, key), selected[parent_id].id, moment.date(), amount))
    if not selected:
        raise NessieImportError("No completed purchases were observed for this merchant and coverage.")
    if len(selected) + len(refunds) > MAX_DATASET_ROWS:
        raise NessieImportError("Selected merchant activity exceeds the 5000-record import limit.")
    orders = tuple(sorted(selected.values(), key=lambda row: (row.occurred_on, row.id)))
    refund_rows = tuple(sorted(refunds, key=lambda row: (row.occurred_on, row.id)))
    data = Dataset(
        orders, refund_rows, coverage_start=coverage_start, coverage_end=coverage_end, as_of=coverage_end,
        synthetic=True, label=f"Nessie v2 sandbox merchant {merchant_id}: synthetic observed records",
        business_name="Nessie sandbox merchant", source_notes=tuple(WARNINGS),
    )
    try:
        _validate(data)
    except ValueError:
        raise NessieImportError("Selected records do not form a valid merchant dataset.") from None
    digest = hashlib.sha256(encode_dataset(data).encode()).hexdigest()
    preview = {
        "preview_hash": digest, "source": "nessie_v2_sandbox", "merchant_id": merchant_id,
        "synthetic": True, "business_name": data.business_name, "orders": len(orders),
        "refunds": len(refund_rows), "customers": len({row.customer_id for row in orders}),
        "gross_revenue_cents": sum(row.amount_cents for row in orders),
        "refund_cents": sum(row.amount_cents for row in refund_rows),
        "net_revenue_cents": sum(row.amount_cents for row in orders) - sum(row.amount_cents for row in refund_rows),
        "coverage_start": coverage_start.isoformat(), "coverage_end": coverage_end.isoformat(),
        "warnings": list(WARNINGS),
    }
    return data, preview


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NessieImportError("Nessie returned duplicate JSON fields.")
        result[key] = value
    return result


def _bad_constant(value):
    raise NessieImportError("Nessie returned non-finite JSON numbers.")


async def _read_json(client, path, *, params=None, byte_budget):
    async with client.stream("GET", path, params=params) as response:
        if response.status_code in {401, 403}:
            raise NessieImportError("Nessie rejected the supplied credential or resource access.")
        if response.status_code != 200:
            raise NessieImportError("Nessie did not return a successful read; no dataset was saved.")
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            byte_budget[0] += len(chunk)
            if len(raw) > MAX_RESPONSE_BYTES or byte_budget[0] > MAX_TOTAL_BYTES:
                raise NessieImportError("Nessie response exceeds the bounded import size.")
        try:
            return json.loads(raw, object_pairs_hook=_object, parse_constant=_bad_constant)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise NessieImportError("Nessie returned invalid JSON.") from None


async def _collect(client, path, byte_budget):
    rows, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = await _read_json(client, path, params=params, byte_budget=byte_budget)
        if not isinstance(page, dict) or "items" not in page or (page["items"] is not None and not isinstance(page["items"], list)):
            raise NessieImportError("Nessie returned an invalid paginated response.")
        rows.extend(page["items"] or [])
        if len(rows) > MAX_ROWS:
            raise NessieImportError("Nessie tenant data exceeds the bounded import size.")
        cursor = page.get("next_cursor", "")
        if cursor in ("", None):
            return rows
        if not isinstance(cursor, str) or len(cursor) > 2048 or cursor in seen:
            raise NessieImportError("Nessie returned an invalid or repeated pagination cursor.")
        seen.add(cursor)
    raise NessieImportError("Nessie pagination exceeded the import limit; no partial dataset is returned.")


async def import_from_nessie(*, merchant_id: str, coverage_start: date, coverage_end: date,
                            api_key: str, transport=None) -> tuple[Dataset, dict]:
    """Read the fixed official API once; transport injection is for offline tests."""
    merchant_id = _identity(merchant_id)
    _coverage(coverage_start, coverage_end)
    if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096:
        raise NessieImportError("Set NESSIE_API_KEY in the process environment before importing.")
    try:
        async with asyncio.timeout(MAX_SECONDS):
            async with httpx.AsyncClient(
                base_url=BASE_URL, headers={"Authorization": f"Bearer {api_key}"},
                timeout=15, follow_redirects=False, trust_env=False, transport=transport,
            ) as client:
                budget = [0]
                merchant, accounts, transactions = await asyncio.gather(
                    _read_json(client, f"/merchants/{quote(merchant_id, safe='')}", byte_budget=budget),
                    _collect(client, "/accounts", budget),
                    _collect(client, "/transactions", budget),
                )
        return normalize_merchant({"merchant": merchant, "accounts": accounts, "transactions": transactions},
                                  merchant_id=merchant_id, coverage_start=coverage_start, coverage_end=coverage_end)
    except NessieImportError:
        raise
    except (httpx.HTTPError, TimeoutError, ValueError):
        raise NessieImportError("Nessie import could not complete; no dataset was saved.") from None
