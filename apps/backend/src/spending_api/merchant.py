"""Deterministic merchant analytics over fictional, identified POS records.

Amounts are integer USD cents. All date boundaries use UTC. No provider or dotenv
settings are imported: this module can run offline and accepts explicit datasets.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import random
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

DEMO_AS_OF = date(2026, 11, 14)
COVERAGE_END = DEMO_AS_OF - timedelta(days=1)
COVERAGE_START = COVERAGE_END - timedelta(days=89)
SEGMENT_RULES = {
    "champions": ("Champions", "At least 10 observed orders and a purchase within 21 days of the observed cutoff."),
    "regulars": ("Regulars", "At least 4 observed orders and a purchase within 30 days of the observed cutoff; not Champions."),
    "promising": ("Promising", "Other customers with at least 2 observed orders, or a single older order."),
    "slipping": ("Slipping", "At least 2 observed orders, with the latest purchase more than 30 days before the observed cutoff."),
    "new": ("New faces", "One observed order, first seen within 14 days of the observed cutoff."),
}


@dataclass(frozen=True)
class Order:
    id: str
    customer_id: str
    occurred_on: date
    amount_cents: int
    status: str = "completed"


@dataclass(frozen=True)
class Refund:
    id: str
    order_id: str
    occurred_on: date
    amount_cents: int


@dataclass(frozen=True)
class Dataset:
    orders: tuple[Order, ...]
    refunds: tuple[Refund, ...]
    coverage_start: date = COVERAGE_START
    coverage_end: date = COVERAGE_END
    as_of: date = DEMO_AS_OF
    label: str = "Fictional cafe POS demonstration"
    synthetic: bool = True
    business_name: str = "Harbor & Pine"
    source_notes: tuple[str, ...] = ()


def demo_dataset() -> Dataset:
    rng = random.Random(20261114)
    orders = []
    # Explicit behavior groups give the owner useful stories without representing
    # actual customers or inferring anyone's income, age, or personal attributes.
    for number in range(1, 121):
        customer = f"C{number:03d}"
        if number <= 25:
            offsets = list(range(number % 5, 90, 4 + number % 3))
        elif number <= 55:
            offsets = list(range(number % 18, 90, 10 + number % 5))
        elif number <= 78:
            offsets = sorted(rng.sample(range(0, 48), 3 + number % 5))
        elif number <= 100:
            offsets = sorted(rng.sample(range(60, 90), 2 + number % 2))
        else:
            offsets = [76 + number % 14]
        for offset in offsets:
            orders.append(Order(f"O{len(orders) + 1:05d}", customer,
                                COVERAGE_START + timedelta(days=offset),
                                rng.choice([575, 725, 950, 1275, 1575, 1825, 2250])))
    orders.sort(key=lambda row: (row.occurred_on, row.id))
    refunds = tuple(Refund(f"R{i:04d}", row.id,
                           min(row.occurred_on + timedelta(days=2), COVERAGE_END),
                           min(row.amount_cents, 575))
                    for i, row in enumerate(orders[::37], 1))
    return Dataset(tuple(orders), refunds)


DEMO = demo_dataset()


def _validate(data: Dataset) -> dict[str, Order]:
    if not isinstance(data.source_notes, tuple) or len(data.source_notes) > 20 or any(
        not isinstance(note, str) or not note.strip() or len(note) > 1000 or "\x00" in note
        for note in data.source_notes
    ):
        raise ValueError("Source notes must be a tuple of at most 20 nonempty strings, each at most 1000 characters")
    if not date(2000, 1, 1) <= data.coverage_start <= data.coverage_end <= date(2100, 12, 31) or not date(2000, 1, 1) <= data.as_of <= date(2100, 12, 31):
        raise ValueError("Supported dates are 2000-01-01 through 2100-12-31")
    if data.coverage_start > data.coverage_end or data.as_of < data.coverage_end:
        raise ValueError("Invalid dataset coverage")
    orders = {}
    for row in data.orders:
        if not row.id.strip() or not row.customer_id.strip() or row.id in orders:
            raise ValueError("Duplicate or missing order/customer identity")
        if type(row.amount_cents) is not int or row.amount_cents < 0:
            raise ValueError("Amounts must be nonnegative integer cents")
        if row.status not in {"completed", "pending", "cancelled"}:
            raise ValueError("Unsupported order status")
        if not data.coverage_start <= row.occurred_on <= data.coverage_end:
            raise ValueError("Order is outside recorded coverage")
        orders[row.id] = row
    ids, totals = set(), defaultdict(int)
    for refund in data.refunds:
        order = orders.get(refund.order_id)
        if not refund.id.strip() or refund.id in ids or order is None:
            raise ValueError("Invalid or duplicate refund identity")
        ids.add(refund.id)
        if type(refund.amount_cents) is not int or refund.amount_cents < 0:
            raise ValueError("Amounts must be nonnegative integer cents")
        if order.status != "completed" or not order.occurred_on <= refund.occurred_on <= data.coverage_end:
            raise ValueError("Refund does not match a completed prior order")
        totals[order.id] += refund.amount_cents
        if totals[order.id] > order.amount_cents:
            raise ValueError("Refunds exceed original order amount")
    return orders


def _segment(count: int, recency: int) -> str:
    if count >= 2 and recency > 30:
        return "slipping"
    if count >= 10 and recency <= 21:
        return "champions"
    if count >= 4 and recency <= 30:
        return "regulars"
    if count == 1 and recency <= 14:
        return "new"
    return "promising"


def _month_offset(first: date, later: date) -> int:
    return (later.year - first.year) * 12 + later.month - first.month


def _month_after(first: date, offset: int) -> date:
    serial = first.year * 12 + first.month - 1 + offset
    return date(serial // 12, serial % 12 + 1, 1)


def build_dashboard(start: date | None = None, end: date | None = None,
                    data: Dataset = DEMO) -> dict:
    orders_by_id = _validate(data)
    end = end or data.coverage_end
    if not date(2000, 1, 1) <= end <= date(2100, 12, 31):
        raise ValueError("Supported dates are 2000-01-01 through 2100-12-31")
    start = start or max(data.coverage_start, end - timedelta(days=29))
    if not date(2000, 1, 1) <= start <= date(2100, 12, 31):
        raise ValueError("Supported dates are 2000-01-01 through 2100-12-31")
    if start > end:
        raise ValueError("Start must be on or before end")
    if (end - start).days > 365:
        raise ValueError("Choose a date range of at most 366 days")
    completed = [row for row in data.orders if row.status == "completed" and row.occurred_on <= end]
    history = defaultdict(list)
    for order in completed:
        history[order.customer_id].append(order)
    for rows in history.values():
        rows.sort(key=lambda row: (row.occurred_on, row.id))
    sales = [row for row in completed if start <= row.occurred_on <= end]
    refunds = [row for row in data.refunds if start <= row.occurred_on <= end]
    period_counts, period_revenue = defaultdict(int), defaultdict(int)
    daily = {start + timedelta(days=i): {"net_revenue_cents": 0, "orders": 0}
             for i in range((end - start).days + 1)}
    for order in sales:
        period_counts[order.customer_id] += 1
        period_revenue[order.customer_id] += order.amount_cents
        daily[order.occurred_on]["net_revenue_cents"] += order.amount_cents
        daily[order.occurred_on]["orders"] += 1
    for refund in refunds:
        period_revenue[orders_by_id[refund.order_id].customer_id] -= refund.amount_cents
        daily[refund.occurred_on]["net_revenue_cents"] -= refund.amount_cents
    segment_as_of = min(end, data.coverage_end)
    customers = []
    for customer_id, rows in sorted(history.items()):
        recency = (segment_as_of - rows[-1].occurred_on).days
        customers.append({"id": customer_id, "label": f"Customer {customer_id}",
                          "first_seen": rows[0].occurred_on.isoformat(),
                          "last_seen": rows[-1].occurred_on.isoformat(),
                          "orders": period_counts[customer_id],
                          "revenue_cents": period_revenue[customer_id],
                          "segment": _segment(len(rows), recency),
                          "days_since_last": recency})
    customers.sort(key=lambda row: (-row["revenue_cents"], row["id"]))
    segments = [{"id": key, "label": label, "description": description,
                 "customers": sum(row["segment"] == key for row in customers),
                 "revenue_cents": sum(row["revenue_cents"] for row in customers if row["segment"] == key)}
                for key, (label, description) in SEGMENT_RULES.items()]
    cohorts_by_month = defaultdict(list)
    for customer_id, rows in history.items():
        cohorts_by_month[rows[0].occurred_on.replace(day=1)].append(customer_id)
    observation_end = min(end, data.coverage_end)
    cohorts = []
    for month, members in sorted(cohorts_by_month.items()):
        retention = []
        for offset in range(4):
            target = _month_after(month, offset)
            last_day = target.replace(day=monthrange(target.year, target.month)[1])
            if offset and last_day > observation_end:
                retention.append(None)
            else:
                active = sum(any(_month_offset(month, row.occurred_on) == offset
                                 for row in history[customer]) for customer in members)
                retention.append(active / len(members))
        cohorts.append({"month": month.strftime("%Y-%m"), "customers": len(members), "retention": retention})
    gross = sum(row.amount_cents for row in sales)
    refunded = sum(row.amount_cents for row in refunds)
    # Count customers from actual sales, not zero-valued table entries.
    active_ids = {row.customer_id for row in sales}
    active = len(active_ids)
    returning = sum(history[customer][0].occurred_on < start for customer in active_ids)
    repeated = sum(period_counts[customer] >= 2 for customer in active_ids)
    actions = []
    templates = {
        "slipping": ("Reconnect with quiet regulars", "Review a small welcome-back offer", "Observed gap over 30 days; this is a rule, not a churn prediction."),
        "new": ("Make a second visit easy", "Test a second-visit invitation", "These customers have one observed order within the last 14 days."),
        "champions": ("Learn from your regulars", "Invite feedback on a new menu item", "Frequent recent buyers can help you test an idea before a wider release."),
    }
    for segment in segments:
        if end <= data.coverage_end and segment["id"] in templates and segment["customers"]:
            title, suggestion, reason = templates[segment["id"]]
            actions.append({"id": f"action-{segment['id']}", "title": title, "reason": reason,
                            "segment": segment["id"], "customers": segment["customers"],
                            "metric": f"{segment['customers']} identified customers", "suggestion": suggestion})
    fingerprint = hashlib.sha256(json.dumps({"metadata": {"label": data.label, "synthetic": data.synthetic, "coverage_start": str(data.coverage_start), "coverage_end": str(data.coverage_end), "as_of": str(data.as_of), "business_name": data.business_name, "source_notes": data.source_notes}, "orders": [(o.id, o.customer_id, str(o.occurred_on), o.amount_cents, o.status) for o in data.orders],
                                            "refunds": [(r.id, r.order_id, str(r.occurred_on), r.amount_cents) for r in data.refunds]}, sort_keys=True).encode()).hexdigest()
    limitations = ["Demonstration records are fictional and do not describe real people or real business performance." if data.synthetic else "Owner-imported records are analyzed as supplied; completeness and accuracy have not been independently verified.",
                   "First seen means first observed in this dataset; earlier purchases are unknown.",
                   "Segments are transparent rules, not churn predictions. Suggested actions have not been tested.",
                   "All analyzed customers have stable IDs; anonymous sales are not supported by this identified-customer dataset."]
    limitations.extend(data.source_notes)
    if start < data.coverage_start or end > data.coverage_end:
        limitations.append("Selected dates extend beyond recorded coverage. Empty days outside coverage are unknown, not confirmed zero sales.")
    if end > data.coverage_end:
        limitations.append(f"Customer recency and groups stop at the observed cutoff {segment_as_of.isoformat()}. Later inactivity is unknown; choose a covered end date before reviewing action suggestions.")
    if start <= data.coverage_start:
        limitations.append("No supplied purchase history precedes this period. A zero returning-customer share does not establish that customers are first-time buyers or that nobody bought twice within the period.")
    return {
        "business": {"id": "cafe-demo" if data is DEMO else "owner-business", "name": data.business_name, "currency": "USD", "timezone": "UTC"},
        "source": {"label": data.label, "synthetic": data.synthetic, "coverage_start": data.coverage_start.isoformat(),
                   "coverage_end": data.coverage_end.isoformat(), "as_of": data.as_of.isoformat(),
                   "revision": fingerprint, "segment_as_of": segment_as_of.isoformat(), "limitations": limitations},
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "metrics": {"net_revenue_cents": gross - refunded, "gross_revenue_cents": gross, "refund_cents": refunded,
                    "order_count": len(sales), "active_customers": active, "returning_customers": returning,
                    "returning_share": returning / active if active else None,
                    "within_period_repeat_customers": repeated,
                    "within_period_repeat_share": repeated / active if active else None,
                    "gross_average_order_cents": int((Decimal(gross) / len(sales)).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if sales else None,
                    "average_order_cents": int((Decimal(gross - refunded) / len(sales)).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if sales else None},
        "trend": [{"date": day.isoformat(), **values} for day, values in daily.items()],
        "segments": segments, "customers": customers, "cohorts": cohorts, "actions": actions,
        "definitions": {
            "revenue": "Completed order totals minus refunds recorded in the selected period, in integer USD cents. Excludes pending/cancelled orders; no tax or profit estimate.",
            "orders": "Completed orders in the selected period. Refunds do not create orders; a fully refunded completed order still counts as a visit.",
            "active_customers": "Distinct identified customers with a completed order in the selected period.",
            "returning_customers": "Active customers whose first observed completed order was before the selected period start. Two first-time purchases within the period do not make a customer returning by this definition.",
            "average_order": "The backwards-compatible average_order_cents is selected-period net revenue divided by completed orders, rounded to cents; undefined when there are no orders. Refunds of earlier orders can make this value negative. It is not average purchase size.",
            "average_purchase": "gross_average_order_cents is gross completed purchase value divided by completed orders in the selected period, rounded to cents before refunds; undefined when there are no orders.",
            "within_period_repeat": "Distinct active customers with at least two completed orders in the selected period, divided by all active customers in that period. Undefined with no active customers. This differs from having a purchase before the period.",
            "segments": f"Segment membership and days since last purchase use all observed completed orders through {segment_as_of.isoformat()}, the earlier of period end and supplied coverage end. They include inactive customers. No inactivity is inferred beyond coverage. Segment revenue and customer order/revenue columns use only the selected period.",
            "cohorts": "First-observed-purchase monthly cohorts, using history through period end. Each cell is the share with a completed order that month; month 0 is 100%. Incomplete later calendar months are null, not zero. Historical coverage may begin mid-month.",
            "dates": "Inclusive UTC calendar dates, supported from 2000-01-01 through 2100-12-31. No events after the selected end contribute to customer history or segments.",
            "actions": "Suggestions for owner review only. No message is sent, no campaign is executed, and no uplift is promised.",
        },
    }


def csv_export(dashboard: dict) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(["customer_id", "customer_label", "segment", "first_seen", "last_seen", "orders", "net_revenue_cents", "days_since_last", "period_start", "period_end", "synthetic"])
    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value
    for row in dashboard["customers"]:
        writer.writerow([safe(row["id"]), safe(row["label"]), safe(row["segment"]), row["first_seen"], row["last_seen"],
                         row["orders"], row["revenue_cents"], row["days_since_last"], dashboard["period"]["start"],
                         dashboard["period"]["end"], dashboard["source"]["synthetic"]])
    return stream.getvalue()


def investigate(question: str, dashboard: dict) -> dict:
    lower = question.casefold()
    if any(word in lower for word in ("experiment", "campaign", "action", "focus", "do next", "plan a", "test a")):
        actions = dashboard["actions"]
        answer = "Review a small, measurable pilot: choose one observed segment, define an offer budget and evaluation window, and compare return visits against a randomly assigned holdout. No customer has been assigned or contacted. The dashboard suggestions are proposals, not evidence of uplift."
        if dashboard["period"]["end"] > dashboard["source"]["coverage_end"]:
            answer = "Choose an end date within supplied coverage before considering a customer action. Later customer inactivity is unknown, so action suggestions are withheld for this window."
        evidence = actions
    elif any(word in lower for word in ("quiet", "risk", "slip", "churn", "bring back", "welcome back", "win back", "stopped returning", "stopped buying")):
        segment = next(row for row in dashboard["segments"] if row["id"] == "slipping")
        cutoff = dashboard["source"]["segment_as_of"]
        ending_unknown = dashboard["period"]["end"] > dashboard["source"]["coverage_end"]
        next_step = "Choose an end date within observed coverage before considering an action; later inactivity is unknown." if ending_unknown else "Review their purchase history before considering a small welcome-back idea."
        answer = f"{segment['customers']} customers have at least two observed orders and an observed purchase gap over 30 days as of {cutoff}. {next_step} This does not prove churn or predict campaign results."
        evidence = [segment]
    elif any(word in lower for word in ("twice", "repeat", "within this period")):
        metrics = dashboard["metrics"]
        count, total = metrics["within_period_repeat_customers"], metrics["active_customers"]
        answer = f"{count} of {total} active customers made at least two completed purchases in this selected period. This is an observed within-period repeat measure, separate from purchases before the window. Earlier customer history remains unknown when it was not supplied."
        evidence = [{"metric": "within_period_repeat_customers", "value": count}, {"metric": "active_customers", "value": total}]
    elif any(word in lower for word in ("return", "loyal")):
        metrics = dashboard["metrics"]
        count, total = metrics["returning_customers"], metrics["active_customers"]
        answer = f"{count} of {total} active customers first appeared before this period. " + dashboard["definitions"]["returning_customers"]
        evidence = [{"metric": "returning_customers", "value": count}, {"metric": "active_customers", "value": total}]
    elif any(word in lower for word in ("revenue", "sales", "money", "order")):
        m = dashboard["metrics"]
        answer = f"Observed net revenue is ${m['net_revenue_cents'] / 100:,.2f} across {m['order_count']} completed orders, after ${m['refund_cents'] / 100:,.2f} in refunds recorded during this period."
        evidence = [{"metric": name, "value": m[name]} for name in ("net_revenue_cents", "order_count", "refund_cents")]
    elif any(word in lower for word in ("cohort", "retention")):
        answer = "The cohort table groups customers by their first observed purchase month. Later incomplete months are unavailable; they are not zero retention."
        evidence = dashboard["cohorts"]
    else:
        return {"status": "clarification", "answer": "Ask about revenue, returning customers, retention cohorts, customers who have gone quiet, or a proposed experiment. This offline investigator uses fixed calculation paths and does not run an AI model.",
                "evidence": [], "limitations": ["Deterministic offline investigation, not an AI-generated answer."],
                "period": dashboard["period"], "source": dashboard["source"], "provider": "deterministic"}
    return {"status": "answered", "answer": answer, "evidence": evidence, "period": dashboard["period"],
            "source": dashboard["source"], "provider": "deterministic",
            "limitations": ["Deterministic offline investigation, not an AI-generated answer.", *dashboard["source"]["limitations"]]}
