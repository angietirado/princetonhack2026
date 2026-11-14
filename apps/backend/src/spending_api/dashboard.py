"""One scoped snapshot supplies every dashboard figure, with amounts kept in cents."""

from collections import defaultdict
from datetime import UTC, datetime

from spending_api.agent_tools import SPENDING_KINDS, SpendingTools
from spending_api.categories import build_category_mapping
from spending_api.models import CategoryTotal, DashboardData, DateRange, SpendingMonth


def build_dashboard(tools: SpendingTools, window: DateRange) -> DashboardData:
    rows = tools.get_transactions(window)
    expenses: dict[str, int] = defaultdict(int)
    months: dict[str, int] = defaultdict(int)
    refunds = 0
    for row in rows:
        month = row.occurred_at.strftime("%Y-%m")
        months.setdefault(month, 0)
        if row.status != "completed":
            continue
        if row.kind in SPENDING_KINDS:
            expenses[row.category] += row.amount_cents
            months[month] += row.amount_cents
        elif row.kind == "refund":
            refunds += row.amount_cents
            months[month] -= row.amount_cents
    accounts = tools.snapshot.accounts
    return DashboardData(
        source=tools.snapshot.source,
        customer_id=accounts[0].customer_id if accounts else None,
        as_of=datetime.now(UTC),
        accounts=accounts,
        cash_balance_cents=sum(
            a.balance_cents for a in accounts if a.type in {"Checking", "Savings"}
        ),
        credit_balance_cents=sum(a.balance_cents for a in accounts if a.type == "Credit Card"),
        summary=tools.summarize_spending(window),
        gross_spending_cents=sum(expenses.values()),
        refunds_cents=refunds,
        expense_categories=[
            CategoryTotal(category=category, amount_cents=amount)
            for category, amount in sorted(expenses.items(), key=lambda item: (-item[1], item[0]))
        ],
        monthly_spending=[
            SpendingMonth(month=month, net_spending_cents=amount)
            for month, amount in sorted(months.items())
        ],
        transactions=rows,
        category_mapping=build_category_mapping(rows),
        recurring=tools.find_recurring_charges(window),
    )
