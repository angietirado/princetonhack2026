"""Frozen financial context and pure, dated wallet projections for the shopping lab.

A projection is a local what-if calculation, never a bank mutation. Historical deposits
are observations, not inferred future income. Only checking-account available cash is
eligible; savings, credit limits, and pending credits are not added to shopping funds.
"""

import copy
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

MAX_CENTS = 9_000_000_000_000
Money = Annotated[StrictInt, Field(ge=0, le=MAX_CENTS)]
SignedMoney = Annotated[StrictInt, Field(ge=-MAX_CENTS, le=MAX_CENTS)]
WalletID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class WalletDataError(ValueError):
    """An incomplete or inconsistent financial context must not become a confident budget."""


def utc_time(value: str | datetime, *, allow_date=False) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            if len(value) == 10 and value[4:5] == "-" and value[7:8] == "-":
                if not allow_date:
                    raise ValueError("A timestamp is required")
                parsed = datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC)
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise WalletDataError("Financial dates must be valid ISO dates or zoned timestamps") from None
    else:
        raise WalletDataError("Financial dates must be strings or aware datetimes")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WalletDataError("Financial timestamps must include a timezone")
    return parsed.astimezone(UTC)


def utc_iso(value, *, allow_date=False):
    return utc_time(value, allow_date=allow_date).isoformat().replace("+00:00", "Z")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class WalletModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class WalletAccount(WalletModel):
    id: WalletID
    type: Literal["Checking", "Savings", "Credit Card"]
    nickname: str = Field(min_length=1, max_length=200)
    balance_cents: SignedMoney
    pending_debits_cents: Money
    available_cents: SignedMoney
    cash_eligible: StrictBool

    @model_validator(mode="after")
    def money_basis(self):
        if self.cash_eligible != (self.type == "Checking"):
            raise ValueError("Only checking accounts are eligible shopping cash")
        if self.type in {"Checking", "Savings"}:
            if self.available_cents != self.balance_cents - self.pending_debits_cents:
                raise ValueError("Available cash must already subtract the reported holds")
        return self


class WalletObligation(WalletModel):
    id: WalletID
    account_id: WalletID
    label: str = Field(min_length=1, max_length=200)
    amount_cents: Money
    due_at: str | None
    created_at: str
    status: Literal["pending", "cancelled", "completed", "recurring"]
    recurring_day: Annotated[StrictInt, Field(ge=1, le=31)] | None = None
    cash_eligible: StrictBool

    @field_validator("due_at")
    @classmethod
    def due_date(cls, value):
        return None if value is None else utc_iso(value, allow_date=True)

    @field_validator("created_at")
    @classmethod
    def created_date(cls, value):
        return utc_iso(value)


class WalletTransaction(WalletModel):
    id: WalletID
    kind: Literal[
        "deposit", "withdrawal", "transfer", "purchase", "bill_payment",
        "fee", "interest_credit", "refund", "chargeback",
    ]
    status: Literal["pending", "completed", "declined", "cancelled"]
    medium: Literal["balance", "rewards"]
    amount_cents: Annotated[StrictInt, Field(ge=1, le=MAX_CENTS)]
    created_at: str
    payer_id: WalletID | None = None
    payee_id: WalletID | None = None
    bill_id: WalletID | None = None
    parent_transaction_id: WalletID | None = None

    @field_validator("created_at")
    @classmethod
    def created_date(cls, value):
        return utc_iso(value)


class WalletSnapshot(WalletModel):
    id: str = Field(min_length=1, max_length=80)
    source: Literal["fixture", "nessie"]
    captured_at: str
    customer_id: WalletID
    available_cents: SignedMoney
    accounts: list[WalletAccount] = Field(max_length=20)
    obligations: list[WalletObligation] = Field(max_length=20_000)
    transactions: list[WalletTransaction] = Field(max_length=20_000)
    assumptions: list[str] = Field(max_length=100)

    @field_validator("captured_at")
    @classmethod
    def captured_date(cls, value):
        return utc_iso(value)

    @model_validator(mode="after")
    def scope_and_cash(self):
        accounts = {account.id: account for account in self.accounts}
        if len(accounts) != len(self.accounts):
            raise ValueError("Account identifiers must be unique")
        if self.available_cents != sum(
            account.available_cents for account in self.accounts if account.cash_eligible
        ):
            raise ValueError("Snapshot cash must equal checking available balances")
        for collection in (self.obligations, self.transactions):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError("Financial record identifiers must be unique")
        for bill in self.obligations:
            if bill.account_id not in accounts:
                raise ValueError("A bill references an account outside the wallet")
            if bill.cash_eligible != accounts[bill.account_id].cash_eligible:
                raise ValueError("Bill cash eligibility must match its account")
        for transaction in self.transactions:
            if not ({transaction.payer_id, transaction.payee_id} & accounts.keys()):
                raise ValueError("A transaction is outside the wallet account scope")
            if utc_time(transaction.created_at) > utc_time(self.captured_at):
                raise ValueError("A transaction cannot occur after snapshot capture")
        return self


def _snapshot_digest(value):
    data = {key: item for key, item in value.items() if key != "id"}
    return "wallet-" + hashlib.sha256(_canonical(data).encode()).hexdigest()


def freeze_wallet(data: dict) -> dict:
    """Copy and fingerprint validated source data for durable per-session storage."""
    if not isinstance(data, dict):
        raise WalletDataError("Wallet source must be an object")
    payload = copy.deepcopy(data)
    payload["id"] = "pending"
    try:
        normalized = WalletSnapshot.model_validate(payload).model_dump(mode="json")
    except ValueError:
        raise WalletDataError("Wallet source data is incomplete, inconsistent, or out of scope") from None
    for field in ("accounts", "obligations", "transactions"):
        normalized[field].sort(key=lambda item: item["id"])
    normalized["id"] = _snapshot_digest(normalized)
    return normalized


def validate_wallet(snapshot: dict) -> dict:
    """Reject tampering rather than quietly calculating against a changed frozen base."""
    normalized = freeze_wallet(snapshot)
    if snapshot.get("id") != normalized["id"]:
        raise WalletDataError("The frozen wallet fingerprint does not match its contents")
    return normalized


class ExpectedIncome(WalletModel):
    id: WalletID
    label: str = Field(min_length=1, max_length=200)
    amount_cents: Annotated[StrictInt, Field(ge=1, le=MAX_CENTS)]
    available_at: str

    @field_validator("available_at")
    @classmethod
    def available_date(cls, value):
        return utc_iso(value, allow_date=True)


class LocalDecision(WalletModel):
    id: WalletID
    kind: Literal["shopping", "expense"]
    label: str = Field(min_length=1, max_length=200)
    amount_cents: Annotated[StrictInt, Field(ge=1, le=MAX_CENTS)]
    occurred_at: str

    @field_validator("occurred_at")
    @classmethod
    def decision_date(cls, value):
        return utc_iso(value, allow_date=True)


class WalletEvent(WalletModel):
    kind: Literal["delay_income", "car_unavailable"]
    income_id: WalletID | None = None
    delay_days: Annotated[StrictInt, Field(ge=1, le=31)] | None = None

    @model_validator(mode="after")
    def valid_event(self):
        if self.kind == "delay_income" and (self.income_id is None or self.delay_days is None):
            raise ValueError("Delayed income needs an income identifier and a day count")
        if self.kind == "car_unavailable" and (self.income_id is not None or self.delay_days is not None):
            raise ValueError("Car availability has no income fields")
        return self


def _obligation_amount(bill, snapshot, accounts):
    """Allocate exact one-off bill payments; recurring occurrence attribution needs review."""
    payments = [
        item for item in snapshot["transactions"]
        if item["kind"] == "bill_payment" and item["bill_id"] == bill["id"]
        and item["payer_id"] == bill["account_id"] and item["medium"] == "balance"
        and item["status"] in {"pending", "completed"}
    ]
    if bill["status"] == "recurring" and payments:
        return None, {
            "id": bill["id"], "code": "recurring_payment_occurrence_unknown",
            "message": "A recurring bill has linked payments but no reliable occurrence identity.",
        }
    if any(utc_time(item["created_at"]) < utc_time(bill["created_at"]) for item in payments):
        return None, {
            "id": bill["id"], "code": "payment_precedes_bill",
            "message": "A linked payment precedes the bill record.",
        }
    payment_ids = {item["id"] for item in payments}
    if any(
        item["parent_transaction_id"] in payment_ids
        and item["kind"] in {"refund", "chargeback"} and item["status"] in {"pending", "completed"}
        for item in snapshot["transactions"]
    ):
        return None, {
            "id": bill["id"], "code": "bill_payment_reversal",
            "message": "A bill payment has a reversal that requires an explicit remaining amount.",
        }
    pending = sum(item["amount_cents"] for item in payments if item["status"] == "pending")
    if pending:
        pending_outflows = sum(
            item["amount_cents"] for item in snapshot["transactions"]
            if item["payer_id"] == bill["account_id"] and item["status"] == "pending"
            and item["medium"] == "balance"
            and item["kind"] in {"purchase", "withdrawal", "transfer", "bill_payment", "fee"}
        )
        if pending_outflows != accounts[bill["account_id"]]["pending_debits_cents"]:
            return None, {
                "id": bill["id"], "code": "pending_hold_mismatch",
                "message": "Recorded pending debits do not reconcile with the account holds.",
            }
    paid = min(bill["amount_cents"], sum(
        item["amount_cents"] for item in payments if item["status"] == "completed"
    ))
    held = min(bill["amount_cents"] - paid, pending)
    return {"remaining": bill["amount_cents"] - paid - held, "paid": paid, "held": held}, None


def project_wallet(
    snapshot: dict, *, days: int = 7, reserve_cents: int = 0,
    expected_income: list[dict] | None = None, events: list[dict] | None = None,
    decisions: list[dict] | None = None,
) -> dict:
    """Project a frozen wallet through a local branch without mutating any input.

    Window: capture instant through midnight UTC after the specified calendar days, end exclusive.
    Past-due unpaid bills are reserved at capture. Recurrence is not inferred or expanded.
    shopping_allowance_cents is end-of-window discretionary cash; spendable_now_cents
    reserves enough for every dated obligation before expected income arrives.
    """
    base = validate_wallet(snapshot)
    if type(days) is not int or not 1 <= days <= 31:
        raise WalletDataError("Projection duration must be an integer from 1 through 31 days")
    if type(reserve_cents) is not int or not 0 <= reserve_cents <= MAX_CENTS:
        raise WalletDataError("Reserve must be a nonnegative integer number of cents")
    if any(value is not None and not isinstance(value, list) for value in (expected_income, events, decisions)):
        raise WalletDataError("Projection records must be lists")
    if any(len(value or []) > 100 for value in (expected_income, events, decisions)):
        raise WalletDataError("Projection input exceeds the 100-record limit")
    try:
        incomes = [ExpectedIncome.model_validate(item).model_dump() for item in expected_income or []]
        branch = [LocalDecision.model_validate(item).model_dump() for item in decisions or []]
        changes = [WalletEvent.model_validate(item).model_dump(exclude_none=True) for item in events or []]
    except ValueError:
        raise WalletDataError("Expected income, local decisions, or events have invalid fields") from None
    if len(incomes) > 100 or len(branch) > 100 or len(changes) > 100:
        raise WalletDataError("Projection input exceeds the 100-record limit")
    if len({item["id"] for item in incomes}) != len(incomes):
        raise WalletDataError("Expected income identifiers must be unique")
    if len({item["id"] for item in branch}) != len(branch):
        raise WalletDataError("Local decision identifiers must be unique")

    start = utc_time(base["captured_at"])
    end = datetime.combine(start.date() + timedelta(days=days), datetime.min.time(), UTC)
    income_by_id = {item["id"]: item for item in incomes}
    delays, car_unavailable = {}, False
    for event in changes:
        if event["kind"] == "car_unavailable":
            if car_unavailable:
                raise WalletDataError("Specify car unavailability only once")
            car_unavailable = True
        else:
            if event["income_id"] not in income_by_id or event["income_id"] in delays:
                raise WalletDataError("Income delays must reference unique explicit income records")
            delays[event["income_id"]] = event["delay_days"]

    accounts = {account["id"]: account for account in base["accounts"]}
    # A payment without bill_id cannot be confidently allocated to an active bill.
    # Only payments created after a relevant bill existed can affect that bill.
    relevant_accounts = {}
    for bill in base["obligations"]:
        if (
            bill["cash_eligible"] and bill["status"] in {"pending", "recurring"}
            and bill["amount_cents"] > 0
            and (bill["due_at"] is None or utc_time(bill["due_at"]) < end)
        ):
            created = utc_time(bill["created_at"])
            current = relevant_accounts.get(bill["account_id"])
            relevant_accounts[bill["account_id"]] = created if current is None else min(current, created)
    ambiguous_accounts, unresolved = set(), []
    for transaction in base["transactions"]:
        account_id = transaction["payer_id"]
        if (
            transaction["kind"] == "bill_payment"
            and transaction["status"] in {"pending", "completed"}
            and transaction["bill_id"] is None
            and account_id in relevant_accounts
            and utc_time(transaction["created_at"]) >= relevant_accounts[account_id]
        ):
            ambiguous_accounts.add(account_id)
            unresolved.append({
                "id": transaction["id"], "code": "bill_payment_link_unknown",
                "message": "A checking-account bill payment has no bill identifier; the remaining obligation is unknown.",
            })
    scheduled, entries, obligations = 0, [], []

    already_paid = already_held = gross = 0
    for bill in base["obligations"]:
        if not bill["cash_eligible"] or bill["status"] in {"completed", "cancelled"}:
            continue
        if bill["due_at"] is None:
            unresolved.append({
                "id": bill["id"], "code": "bill_due_date_unknown",
                "message": "An active checking-account bill has no reliable upcoming payment date.",
            })
            continue
        due = utc_time(bill["due_at"])
        if due >= end:
            continue
        if bill["account_id"] in ambiguous_accounts:
            continue
        allocation, issue = _obligation_amount(bill, base, accounts)
        if issue:
            unresolved.append(issue)
            continue
        scheduled += allocation["remaining"]
        gross += bill["amount_cents"]
        already_paid += allocation["paid"]
        already_held += allocation["held"]
        effective = max(due, start)
        obligations.append({
            "id": bill["id"], "label": bill["label"], "due_at": bill["due_at"],
            "projection_at": utc_iso(effective), "gross_cents": bill["amount_cents"],
            "remaining_cents": allocation["remaining"], "already_paid_cents": allocation["paid"],
            "already_held_cents": allocation["held"],
        })
        if allocation["remaining"]:
            entries.append({
                "id": bill["id"], "kind": "obligation", "label": bill["label"],
                "at": utc_iso(effective), "delta_cents": -allocation["remaining"],
            })

    income_total = deferred = local_spending = 0
    for income in incomes:
        original = utc_time(income["available_at"])
        if original < start:
            raise WalletDataError("Expected income cannot precede the frozen snapshot")
        adjusted = original + timedelta(days=delays.get(income["id"], 0))
        if original < end <= adjusted:
            deferred += income["amount_cents"]
        if adjusted < end:
            income_total += income["amount_cents"]
            entries.append({
                "id": income["id"], "kind": "expected_income", "label": income["label"],
                "at": utc_iso(adjusted), "delta_cents": income["amount_cents"],
            })
    for decision in branch:
        when = utc_time(decision["occurred_at"])
        if when < start:
            raise WalletDataError("Local decisions cannot precede the frozen snapshot")
        if when < end:
            local_spending += decision["amount_cents"]
            entries.append({
                "id": decision["id"], "kind": decision["kind"], "label": decision["label"],
                "at": utc_iso(when), "delta_cents": -decision["amount_cents"],
            })
    # Outflows first at equal timestamps avoids spending income whose intraday order is unknown.
    entries.sort(key=lambda item: (utc_time(item["at"]), item["delta_cents"] >= 0, item["kind"], item["id"]))
    balance = minimum = base["available_cents"]
    for entry in entries:
        balance += entry["delta_cents"]
        minimum = min(minimum, balance)
        entry["balance_cents"] = balance
    known_end = base["available_cents"] - scheduled + income_total - local_spending
    assert balance == known_end
    incomplete = bool(unresolved)
    assumptions = [
        *base["assumptions"],
        "Only checking available balances are cash; holds are already deducted once.",
        "Checking balances are pooled, assuming transfers between them have no fees or delay; this does not guarantee solvency of each individual bill-paying account.",
        "Historical transactions are context, not replayed against the captured balance.",
        "Expected income is explicit scenario input; deposits are never automatically called income.",
        "Only each bill's reported next occurrence is modeled; no monthly recurrence is invented.",
        "Date-only bill deadlines mean midnight UTC; unpaid overdue bills are reserved at capture.",
        "At equal timestamps, obligations and local spending precede expected income.",
        "Local shopping and expense decisions are simulated ledger entries, not bank writes.",
        "Loans or expenses absent from the captured bills and explicit inputs are not modeled.",
    ]
    if any(account["type"] != "Checking" for account in base["accounts"]):
        assumptions.append("Savings balances and unused credit are excluded from shopping funds.")
    if car_unavailable:
        assumptions.append("Car unavailability removes driving for this branch; it invents no new fare.")
    if incomplete:
        assumptions.append("Unresolved obligations prevent a reliable allowance; known totals are partial.")
    branch_input = {
        "snapshot_id": base["id"], "days": days, "reserve_cents": reserve_cents,
        "expected_income": incomes, "events": changes, "decisions": branch,
    }
    return {
        "id": "projection-" + hashlib.sha256(_canonical(branch_input).encode()).hexdigest(),
        "snapshot_id": base["id"], "source": base["source"], "status": "needs_review" if incomplete else "ready",
        "window_start": utc_iso(start), "window_end_exclusive": utc_iso(end), "days": days,
        "baseline_available_cents": base["available_cents"],
        "gross_scheduled_obligations_cents": gross, "scheduled_obligations_cents": scheduled,
        "already_paid_bill_payments_cents": already_paid,
        "already_held_bill_payments_cents": already_held,
        "expected_income_cents": income_total, "deferred_income_cents": deferred,
        "local_spending_cents": local_spending, "reserve_cents": reserve_cents,
        "ending_available_cents": None if incomplete else known_end,
        "known_events_ending_available_cents": known_end,
        "shopping_allowance_cents": None if incomplete else max(0, known_end - reserve_cents),
        "spendable_now_cents": None if incomplete else max(0, minimum - reserve_cents),
        "shortfall_cents": None if incomplete else max(0, -minimum),
        "reserve_shortfall_cents": None if incomplete else max(0, reserve_cents - minimum),
        "minimum_available_cents": None if incomplete else minimum,
        "timeline": entries, "obligations": obligations, "unresolved": unresolved,
        "assumptions": assumptions, "allowed_travel_modes": ["walk"] if car_unavailable else ["walk", "drive"],
    }


def make_fixture_wallets(captured_at="2026-11-14T00:00:00Z") -> list[dict]:
    """Three unmistakably fictional account/bill snapshots; no claim of a live Nessie capture."""
    captured = utc_time(captured_at)
    profiles = [
        ("steady", "Steady week", 42000, 2000, 18000),
        ("tight", "Tight week", 15500, 1500, 11000),
        ("buffer", "Building a buffer", 65000, 5000, 24000),
    ]
    result = []
    for key, label, posted, holds, bill_amount in profiles:
        account_id = f"fixture-{key}-checking"
        result.append(freeze_wallet({
            "source": "fixture", "captured_at": utc_iso(captured), "customer_id": f"fixture-{key}",
            "available_cents": posted - holds,
            "accounts": [
                {"id": account_id, "type": "Checking", "nickname": label,
                 "balance_cents": posted, "pending_debits_cents": holds,
                 "available_cents": posted - holds, "cash_eligible": True},
                {"id": f"fixture-{key}-credit", "type": "Credit Card", "nickname": "Example card",
                 "balance_cents": 30000, "pending_debits_cents": 0,
                 "available_cents": 170000, "cash_eligible": False},
            ],
            "obligations": [{
                "id": f"fixture-{key}-bill", "account_id": account_id,
                "label": "Fictional utility and phone payment", "amount_cents": bill_amount,
                "due_at": utc_iso(captured + timedelta(days=2)), "created_at": utc_iso(captured),
                "status": "pending", "recurring_day": None, "cash_eligible": True,
            }],
            "transactions": [{
                "id": f"fixture-{key}-hold", "kind": "purchase", "status": "pending",
                "medium": "balance", "amount_cents": holds, "created_at": utc_iso(captured),
                "payer_id": account_id, "payee_id": None, "bill_id": None, "parent_transaction_id": None,
            }],
            "assumptions": [
                "Fictional wallet fixture created locally for the experiment; not fetched from Nessie.",
                "All people, balances, bills, and transactions in this fixture are invented.",
                "The fixture timestamp is a scenario anchor, not evidence of an actual bank capture.",
                "No future income is assumed unless explicitly supplied to a projection.",
            ],
        }))
    return result




