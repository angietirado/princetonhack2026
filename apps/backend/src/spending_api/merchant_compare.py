"""Observed period comparisons with exact customer contribution reconciliation."""
from __future__ import annotations

from datetime import date, timedelta
from spending_api.merchant import Dataset, build_dashboard


def compare_periods(data: Dataset, start: date | None = None, end: date | None = None) -> dict:
    current = build_dashboard(start, end, data)
    finish = date.fromisoformat(current['period']['end'])
    begin = date.fromisoformat(current['period']['start'])
    days = (finish - begin).days + 1
    prior_end = begin - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)
    baseline = build_dashboard(prior_start, prior_end, data)
    prior_rows = {row['id']: row for row in baseline['customers']}
    now_rows = {row['id']: row for row in current['customers']}
    contributions = []
    groups = {key: {'id': key, 'label': label, 'customers': 0, 'delta_cents': 0,
                    'baseline_cents': 0, 'current_cents': 0} for key, label in (
        ('first_observed', 'First observed customers'),
        ('returned', 'Previously observed, active again'),
        ('continued', 'Purchased in both periods'),
        ('went_quiet', 'Purchased before, no new order now'),
        ('refund_only', 'Refund adjustments without period purchases'),
    )}
    for key in sorted(set(prior_rows) | set(now_rows)):
        before, now = prior_rows.get(key, {}), now_rows.get(key, {})
        a, b = before.get('revenue_cents', 0), now.get('revenue_cents', 0)
        a_orders, b_orders = before.get('orders', 0), now.get('orders', 0)
        if not a_orders and not b_orders and not a and not b:
            continue
        if b_orders and not a_orders:
            group = 'first_observed' if now['first_seen'] >= begin.isoformat() else 'returned'
        elif a_orders and b_orders:
            group = 'continued'
        elif a_orders:
            group = 'went_quiet'
        else:
            group = 'refund_only'
        change = b - a
        item = {'customer_id': key, 'group': group, 'baseline_cents': a,
                'current_cents': b, 'delta_cents': change,
                'baseline_orders': a_orders, 'current_orders': b_orders}
        contributions.append(item)
        g = groups[group]
        g['customers'] += 1
        g['baseline_cents'] += a
        g['current_cents'] += b
        g['delta_cents'] += change
    previous = baseline['metrics']['net_revenue_cents']
    present = current['metrics']['net_revenue_cents']
    delta = present - previous
    contributions.sort(key=lambda item: (-abs(item['delta_cents']), item['customer_id']))
    covered = prior_start >= data.coverage_start and finish <= data.coverage_end
    limitations = [
        'This comparison describes observed purchases and refund timing, not the cause of a change.',
        'First observed does not prove a customer is new to the business.',
        'Groups are defined by purchases in these two periods; they are separate from lifetime recency segments.',
        'Net revenue is not profit. Refunds follow their recorded date and may relate to an earlier sale.',
    ]
    if not covered:
        limitations.append('At least one period extends beyond supplied coverage; empty dates are unknown. Rate and percentage comparisons are unavailable.')
    total = sum(row['delta_cents'] for row in contributions)
    if total != delta:
        raise ValueError('Customer contributions do not reconcile to the total change.')
    return {
        'baseline': {'period': baseline['period'], 'metrics': baseline['metrics']},
        'current': {'period': current['period'], 'metrics': current['metrics']},
        'delta_cents': delta,
        'change_fraction': delta / previous if previous > 0 and covered else None,
        'fully_covered': covered, 'days_per_period': days,
        'groups': list(groups.values()), 'contributors': contributions[:20],
        'omitted_contributors': max(0, len(contributions) - 20),
        'omitted_delta_cents': sum(row['delta_cents'] for row in contributions[20:]),
        'reconciled_delta_cents': total,
        'source': current['source'], 'limitations': limitations,
    }
