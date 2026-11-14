"""Provider-neutral tools: bind an authorized snapshot before exposing to an LLM."""

from collections import defaultdict

from spending_api.models import (
    CategoryTotal,
    DateRange,
    RecurringCharge,
    Snapshot,
    SpendingSummary,
    Transaction,
)

SPENDING_KINDS = {"purchase", "bill_payment", "fee", "chargeback"}


class SpendingTools:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot

    def get_transactions(self, window: DateRange) -> list[Transaction]:
        """Get scoped transactions within inclusive UTC dates."""
        if window.start and window.end and window.start > window.end:
            raise ValueError("start must be on or before end")
        return [
            tx
            for tx in self.snapshot.transactions
            if (not window.start or tx.occurred_at.date() >= window.start)
            and (not window.end or tx.occurred_at.date() <= window.end)
        ]

    def summarize_spending(self, window: DateRange) -> SpendingSummary:
        """Compute completed expenses minus refunds, excluding transfers and cash withdrawals."""
        rows = self.get_transactions(window)
        categories: dict[str, int] = defaultdict(int)
        included = 0
        for tx in rows:
            if tx.status != "completed" or tx.kind not in SPENDING_KINDS | {"refund"}:
                continue
            included += 1
            categories[tx.category] += tx.amount_cents * (-1 if tx.kind == "refund" else 1)
        dates = [tx.occurred_at.date() for tx in rows]
        return SpendingSummary(
            source=self.snapshot.source,
            start=window.start or min(dates, default=None),
            end=window.end or max(dates, default=None),
            net_spending_cents=sum(categories.values()),
            included_count=included,
            excluded_count=len(rows) - included,
            categories=[
                CategoryTotal(category=key, amount_cents=value)
                for key, value in sorted(categories.items(), key=lambda item: (-item[1], item[0]))
            ],
        )

    def find_recurring_charges(self, window: DateRange) -> list[RecurringCharge]:
        """Find candidate monthly purchases with equal amounts and at least three occurrences."""
        groups: dict[tuple[str, str, int], list[Transaction]] = defaultdict(list)
        for tx in self.get_transactions(window):
            if tx.kind == "purchase" and tx.status == "completed" and tx.merchant_id:
                groups[(tx.account_id, tx.merchant_id, tx.amount_cents)].append(tx)
        results = []
        for (_, _, amount), rows in groups.items():
            rows.sort(key=lambda tx: tx.occurred_at)
            gaps = [
                (b.occurred_at.date() - a.occurred_at.date()).days for a, b in zip(rows, rows[1:])
            ]
            if len(rows) >= 3 and all(25 <= gap <= 35 for gap in gaps):
                results.append(
                    RecurringCharge(
                        merchant=rows[0].merchant,
                        amount_cents=amount,
                        occurrences=len(rows),
                        interval_days=round(sum(gaps) / len(gaps)),
                        transaction_ids=[tx.id for tx in rows],
                    )
                )
        return sorted(results, key=lambda row: (-row.amount_cents, row.merchant))
