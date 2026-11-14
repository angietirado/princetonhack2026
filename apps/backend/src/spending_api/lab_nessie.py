"""Read-only Nessie v2 adapter with a fixed HTTPS origin and pinned customer scope.

Source contract: https://v2-prod-api.nessieisreal.com/openapi.json (reviewed 2026-11-14).
Account available_cents already excludes holds; card availability is credit, not cash.
Bill payment_amount is dollars, unlike transaction amount_cents. No banking writes,
automatic synthetic fallback, dotenv loading, or model-selected account/customer IDs.
"""

import asyncio
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import SecretStr

from spending_api.lab_wallet import MAX_CENTS, WalletDataError, freeze_wallet, utc_iso

NESSIE_BASE_URL = "https://v2-prod-api.nessieisreal.com"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class NessieReadError(WalletDataError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Nessie financial context could not be captured: {code}")


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise NessieReadError("invalid_identifier")
    return value


def _optional_identifier(value):
    return None if value is None else _identifier(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _unique_records(rows):
    if not isinstance(rows, list):
        raise NessieReadError("invalid_record_list")
    unique, fingerprints = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            raise NessieReadError("invalid_record")
        identity = _identifier(row.get("id"))
        try:
            signature = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise NessieReadError("invalid_record") from None
        if identity in fingerprints and fingerprints[identity] != signature:
            raise NessieReadError("conflicting_duplicate")
        unique.setdefault(identity, row)
        fingerprints[identity] = signature
    return unique


def dollars_to_cents(value):
    """Convert numeric dollar amounts exactly; do not invent rounding for sub-cent bills."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise NessieReadError("invalid_bill_dollars")
    try:
        dollars = Decimal(str(value))
        if not dollars.is_finite() or dollars < 0 or dollars > Decimal(MAX_CENTS) / 100:
            raise NessieReadError("invalid_bill_dollars")
        cents = dollars * 100
        if cents != cents.to_integral_value():
            raise NessieReadError("subcent_bill_amount")
        return int(cents)
    except InvalidOperation:
        raise NessieReadError("invalid_bill_dollars") from None


def normalize_nessie_snapshot(raw: dict, *, customer_id: str, captured_at: str | datetime) -> dict:
    """Normalize only records attributable to the configured customer's verified accounts."""
    customer_id = _identifier(customer_id)
    if not isinstance(raw, dict) or set(raw) != {"accounts", "bills", "transactions"}:
        raise NessieReadError("invalid_capture_shape")
    account_rows = _unique_records(raw["accounts"])
    bill_rows = _unique_records(raw["bills"])
    transaction_rows = _unique_records(raw["transactions"])
    accounts = []
    for row in account_rows.values():
        if _identifier(row.get("customer_id")) != customer_id:
            raise NessieReadError("cross_customer_account")
        if row.get("type") not in {"Checking", "Savings", "Credit Card"}:
            raise NessieReadError("unsupported_account_type")
        accounts.append({
            "id": row["id"], "type": row["type"], "nickname": row.get("nickname") or "Unnamed account",
            "balance_cents": row.get("balance_cents"),
            "pending_debits_cents": row.get("pending_debits_cents"),
            "available_cents": row.get("available_cents"),
            "cash_eligible": row["type"] == "Checking",
        })
    owned = {row["id"]: row for row in accounts}
    bills = []
    for row in bill_rows.values():
        account_id = _identifier(row.get("account_id"))
        if account_id not in owned:
            raise NessieReadError("cross_customer_bill")
        upcoming = row.get("upcoming_payment_date")
        fallback = row.get("payment_date")
        if upcoming is not None and not isinstance(upcoming, str):
            raise NessieReadError("invalid_bill_date")
        if fallback is not None and not isinstance(fallback, str):
            raise NessieReadError("invalid_bill_date")
        due = upcoming or fallback or None
        bills.append({
            "id": row["id"], "account_id": account_id,
            "label": row.get("nickname") or row.get("payee"),
            "amount_cents": dollars_to_cents(row.get("payment_amount")),
            "due_at": utc_iso(due, allow_date=True) if due else None,
            "created_at": utc_iso(row.get("created_at")),
            "status": row.get("status"), "recurring_day": row.get("recurring_date"),
            "cash_eligible": owned[account_id]["cash_eligible"],
        })
    transactions = []
    for row in transaction_rows.values():
        payer, payee = _optional_identifier(row.get("payer_id")), _optional_identifier(row.get("payee_id"))
        if payer not in owned and payee not in owned:
            continue
        transactions.append({
            "id": row["id"], "kind": row.get("kind"), "status": row.get("status"),
            "medium": row.get("medium"), "amount_cents": row.get("amount_cents"),
            "created_at": utc_iso(row.get("created_at")), "payer_id": payer, "payee_id": payee,
            "bill_id": _optional_identifier(row.get("bill_id")),
            "parent_transaction_id": _optional_identifier(row.get("parent_transaction_id")),
        })
    try:
        available = sum(row["available_cents"] for row in accounts if row["cash_eligible"])
        return freeze_wallet({
            "source": "nessie", "captured_at": utc_iso(captured_at), "customer_id": customer_id,
            "available_cents": available, "accounts": accounts, "obligations": bills,
            "transactions": transactions,
            "assumptions": [
                "Read-only capture of a pinned customer's Nessie v2 synthetic banking sandbox.",
                "Nessie is simulated banking data; this is not evidence of a real person's finances.",
                "Account available_cents already subtracts pending debit holds.",
                "Credit Card available_cents is remaining credit and is never shopping cash.",
                "Bill payment_amount is dollars, converted exactly to integer cents; sub-cent values are rejected.",
                "Historical deposits, transfers, refunds, and pending credits are not assumed future income.",
                "An empty or missing upcoming bill date remains unknown; recurring_date is not a frequency.",
                "Only listed accounts, bills, and bounded transaction history are captured; loans are not captured.",
                "HTTP reads are not an atomic bank snapshot; account balances are checked before and after capture.",
            ],
        })
    except (TypeError, ValueError):
        raise NessieReadError("invalid_financial_data") from None


class NessieReadClient:
    """Server-configured, bounded reader. The optional transport is for mock/offline tests only."""

    def __init__(
        self, *, api_key: str, customer_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10, total_timeout_seconds: float = 30,
        max_pages: int = 20, max_records: int = 20_000, max_accounts: int = 20,
    ):
        if not isinstance(api_key, str) or not api_key.strip() or any(ord(char) < 32 for char in api_key):
            raise NessieReadError("missing_api_key")
        self._key = SecretStr(api_key)
        self.customer_id = _identifier(customer_id)
        self.transport = transport
        for name, value, maximum in (
            ("max_pages", max_pages, 100), ("max_records", max_records, 20_000),
            ("max_accounts", max_accounts, 20),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} is outside the supported capture limit")
        if (
            isinstance(timeout_seconds, bool) or isinstance(total_timeout_seconds, bool)
            or not 0 < timeout_seconds <= 30 or not 0 < total_timeout_seconds <= 90
        ):
            raise ValueError("Capture timeouts must be positive and bounded")
        self.timeout_seconds, self.total_timeout_seconds = timeout_seconds, total_timeout_seconds
        self.max_pages, self.max_records, self.max_accounts = max_pages, max_records, max_accounts

    async def _pages(self, client, path, budget, *, page_size=100):
        rows, cursor, seen = [], None, set()
        for _ in range(self.max_pages):
            budget["requests"] += 1
            if budget["requests"] > 100:
                raise NessieReadError("request_limit")
            params = {"limit": page_size}
            if cursor is not None:
                params["cursor"] = cursor
            async with client.stream("GET", path, params=params) as response:
                if response.status_code in {401, 403}:
                    raise NessieReadError("access_denied")
                if response.status_code == 404:
                    raise NessieReadError("resource_not_found")
                if response.status_code != 200:
                    raise NessieReadError("upstream_unavailable")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise NessieReadError("response_size_limit")
            try:
                page = json.loads(
                    body.decode("utf-8"), object_pairs_hook=_unique_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
                )
            except (ValueError, UnicodeError, RecursionError):
                raise NessieReadError("invalid_json") from None
            if not isinstance(page, dict) or "items" not in page:
                raise NessieReadError("invalid_page")
            items = page["items"]
            if items is None:
                items = []
            if not isinstance(items, list) or len(items) > page_size:
                raise NessieReadError("invalid_page")
            rows.extend(items)
            if len(rows) > self.max_records:
                raise NessieReadError("record_limit")
            cursor = page.get("next_cursor")
            if cursor is None or cursor == "":
                return list(_unique_records(rows).values())
            if not isinstance(cursor, str) or len(cursor) > 2048 or not cursor.strip():
                raise NessieReadError("invalid_cursor")
            if cursor in seen:
                raise NessieReadError("repeated_cursor")
            seen.add(cursor)
        raise NessieReadError("page_limit")

    async def capture(self) -> dict:
        """Fetch a fresh context; callers persist the returned frozen snapshot once per session."""
        try:
            async with asyncio.timeout(self.total_timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=NESSIE_BASE_URL, transport=self.transport, follow_redirects=False,
                    trust_env=False, timeout=self.timeout_seconds,
                    headers={"Authorization": "Bearer " + self._key.get_secret_value()},
                ) as client:
                    budget = {"requests": 0}
                    account_path = f"/customers/{self.customer_id}/accounts"
                    accounts = await self._pages(client, account_path, budget)
                    if len(accounts) > self.max_accounts:
                        raise NessieReadError("account_limit")
                    for account in accounts:
                        if _identifier(account.get("customer_id")) != self.customer_id:
                            raise NessieReadError("cross_customer_account")
                    bills = []
                    for account in accounts:
                        identity = _identifier(account.get("id"))
                        scoped = await self._pages(client, f"/accounts/{identity}/bills", budget)
                        if any(item.get("account_id") != identity for item in scoped):
                            raise NessieReadError("cross_account_bill")
                        bills.extend(scoped)
                        if len(bills) > self.max_records:
                            raise NessieReadError("record_limit")
                    transactions = await self._pages(client, "/transactions", budget, page_size=1000)
                    # Guard against observing a changed cash baseline midway through the read.
                    confirmed = await self._pages(client, account_path, budget)
                    def signature(rows):
                        keys = (
                            "id", "customer_id", "type", "balance_cents",
                            "pending_debits_cents", "available_cents", "updated_at",
                        )
                        return sorted(
                            (json.dumps({key: row.get(key) for key in keys}, sort_keys=True)
                             for row in rows)
                        )
                    if signature(accounts) != signature(confirmed):
                        raise NessieReadError("accounts_changed_during_capture")
                    return normalize_nessie_snapshot(
                        {"accounts": accounts, "bills": bills, "transactions": transactions},
                        customer_id=self.customer_id, captured_at=datetime.now(UTC),
                    )
        except NessieReadError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise NessieReadError("timeout") from None
        except httpx.HTTPError:
            raise NessieReadError("network_error") from None
        except (ValueError, TypeError):
            raise NessieReadError("invalid_financial_data") from None


