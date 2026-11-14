"""Customer-scoped evidence from one immutable merchant dataset.

Amounts are unsigned cents; event kind determines whether money adds or subtracts.
History is bounded by the selected end and coverage, while period totals retain the
same inclusive UTC window and refund-date accounting as the owner dashboard.
"""
from __future__ import annotations

import re
from datetime import date

from spending_api.merchant import Dataset, build_dashboard


class CustomerNotFound(LookupError):
    """No completed purchase for this customer has been observed by the cutoff."""


class CustomerRevisionMismatch(ValueError):
    """The caller's reviewed dataset differs from the bound dataset."""


def customer_detail(data: Dataset, customer_id: str, start: date | None = None,
                    end: date | None = None, offset: int = 0, limit: int = 50,
                    *, expected_revision: str | None = None) -> dict:
    if not isinstance(customer_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", customer_id):
        raise ValueError("Use an opaque customer ID of at most 80 characters")
    if type(offset) is not int or not 0 <= offset <= 5000:
        raise ValueError("Offset must be a whole number from 0 to 5000")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Limit must be a whole number from 1 to 100")
    if expected_revision is not None and (
        not isinstance(expected_revision, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_revision)
    ):
        raise ValueError("Expected revision must be a 64-character SHA-256 digest")

    dashboard = build_dashboard(start, end, data)
    # Check before customer lookup: switching datasets must produce a stale-view
    # diagnostic even if the old customer's ID does not exist in the new dataset.
    if expected_revision is not None and expected_revision != dashboard["source"]["revision"]:
        raise CustomerRevisionMismatch("The dataset changed; refresh before opening customer evidence")
    customer = next((row for row in dashboard["customers"] if row["id"] == customer_id), None)
    if customer is None:
        raise CustomerNotFound("Customer is not observed by the selected cutoff")

    begin = date.fromisoformat(dashboard["period"]["start"])
    finish = date.fromisoformat(dashboard["period"]["end"])
    cutoff = min(finish, data.coverage_end)
    purchases = [row for row in data.orders if row.customer_id == customer_id
                 and row.status == "completed" and row.occurred_on <= cutoff]
    order_ids = {row.id for row in purchases}
    refunds = [row for row in data.refunds if row.order_id in order_ids and row.occurred_on <= cutoff]
    period_purchases = [row for row in purchases if begin <= row.occurred_on <= finish]
    period_refunds = [row for row in refunds if begin <= row.occurred_on <= finish]

    def metrics(orders, adjustments):
        gross = sum(row.amount_cents for row in orders)
        refunded = sum(row.amount_cents for row in adjustments)
        return {"orders": len(orders), "gross_revenue_cents": gross,
                "refund_cents": refunded, "net_revenue_cents": gross - refunded}

    history = {**metrics(purchases, refunds), "first_seen": customer["first_seen"],
               "last_seen": customer["last_seen"]}
    period_metrics = metrics(period_purchases, period_refunds)
    if (period_metrics["orders"] != customer["orders"]
            or period_metrics["net_revenue_cents"] != customer["revenue_cents"]):
        raise ValueError("Customer evidence does not reconcile with the dashboard")
    events = [
        {"id": row.id, "kind": "purchase", "date": row.occurred_on.isoformat(),
         "amount_cents": row.amount_cents, "order_id": row.id,
         "within_period": begin <= row.occurred_on <= finish}
        for row in purchases
    ] + [
        {"id": row.id, "kind": "refund", "date": row.occurred_on.isoformat(),
         "amount_cents": row.amount_cents, "order_id": row.order_id,
         "within_period": begin <= row.occurred_on <= finish}
        for row in refunds
    ]
    # Kind participates in the key because order and refund IDs occupy separate
    # namespaces. This makes equal-date pagination deterministic and lossless.
    events.sort(key=lambda row: (row["date"], row["kind"], row["id"]), reverse=True)
    total = len(events)
    selected = events[offset:offset + limit]
    next_offset = offset + len(selected) if offset + len(selected) < total else None
    segment = next(row for row in dashboard["segments"] if row["id"] == customer["segment"])
    return {
        "source": dashboard["source"], "period": dashboard["period"], "customer": customer,
        "segment": {"id": segment["id"], "label": segment["label"],
                    "description": segment["description"], "as_of": cutoff.isoformat()},
        "observed_history": history, "period_metrics": period_metrics,
        "events": selected,
        "pagination": {"offset": offset, "limit": limit, "total": total, "next_offset": next_offset},
    }