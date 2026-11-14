from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, Field

Cents = Annotated[int, Field(strict=True, ge=0)]


class Account(BaseModel):
    id: str
    customer_id: str
    nickname: str
    type: str
    balance_cents: int


class Transaction(BaseModel):
    id: str
    account_id: str
    kind: str
    status: str
    amount_cents: Cents
    occurred_at: datetime
    merchant: str
    merchant_id: str | None = None
    category: str
    description: str = ""


class Snapshot(BaseModel):
    source: str
    accounts: list[Account]
    transactions: list[Transaction]


class DateRange(BaseModel):
    start: date | None = None
    end: date | None = None


class CategoryTotal(BaseModel):
    category: str
    amount_cents: int


class SpendingSummary(BaseModel):
    source: str
    currency: str = "USD"
    start: date | None
    end: date | None
    net_spending_cents: int
    included_count: int
    excluded_count: int
    categories: list[CategoryTotal]


class RecurringCharge(BaseModel):
    merchant: str
    amount_cents: int
    occurrences: int
    interval_days: int
    transaction_ids: list[str]
    confidence: str = "candidate"


class SpendingMonth(BaseModel):
    month: str
    net_spending_cents: int


class DashboardData(BaseModel):
    source: str
    customer_id: str | None
    as_of: datetime
    accounts: list[Account]
    cash_balance_cents: int
    credit_balance_cents: int
    summary: SpendingSummary
    gross_spending_cents: int
    refunds_cents: int
    expense_categories: list[CategoryTotal]
    monthly_spending: list[SpendingMonth]
    transactions: list[Transaction]
    recurring: list[RecurringCharge]
