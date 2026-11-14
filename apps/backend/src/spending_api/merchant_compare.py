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


COMPARISON_GROUPS = {
    'first_observed': 'First observed customers',
    'returned': 'Previously observed, active again',
    'continued': 'Purchased in both periods',
    'went_quiet': 'Purchased before, no new order now',
    'refund_only': 'Refund adjustments without period purchases',
}
PRIOR_PERIOD_UNAVAILABLE = 'The preceding equal-length period is outside the supported 2000–2100 calendar.'
COMPARISON_METRICS = ('net_revenue_cents', 'gross_revenue_cents', 'refund_cents',
                      'order_count', 'active_customers')


def _comparison_int(value, *, nonnegative=False):
    if type(value) is not int or abs(value) > 10**15 or (nonnegative and value < 0):
        raise ValueError('Invalid aggregate comparison number.')
    return value


def _comparison_day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('Invalid comparison date.')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Invalid comparison date.')
    return parsed


def _comparison_period(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid comparison period.')
    start, end = (_comparison_day(value[key]) for key in ('start', 'end'))
    if start > end or (end - start).days > 365:
        raise ValueError('Invalid comparison period.')
    return {'start': start.isoformat(), 'end': end.isoformat()}


def aggregate_comparison(snapshot: dict) -> dict:
    """Validate binding and project only complete, reconciling aggregate groups.

    Customer contributors, arbitrary labels, source prose, and supplied percentage
    fields never enter this evidence. Percentages derive from integer totals.
    """
    comparison = snapshot.get('comparison')
    if comparison is None:
        unsupported = snapshot.get('comparison_unavailable_reason') == PRIOR_PERIOD_UNAVAILABLE
        return {
            'available': False, 'reason': 'unsupported_prior_period' if unsupported else 'comparison_not_bound',
            'limitations': [PRIOR_PERIOD_UNAVAILABLE if unsupported else
                            'No preceding-period comparison is bound to this snapshot. Revenue change cannot be inferred from one period.'],
        }
    if not isinstance(comparison, dict):
        raise ValueError('Invalid comparison payload.')
    source, other_source = snapshot['source'], comparison['source']
    revision = source.get('revision')
    if (not isinstance(revision, str) or len(revision) != 64
            or any(character not in '0123456789abcdef' for character in revision)
            or other_source.get('revision') != revision):
        raise ValueError('Comparison does not match this source revision.')
    coverage_start, coverage_end = (_comparison_day(source[key]) for key in ('coverage_start', 'coverage_end'))
    if coverage_start > coverage_end or type(source['synthetic']) is not bool:
        raise ValueError('Invalid comparison source coverage.')
    if any(other_source.get(key) != source[key] for key in ('coverage_start', 'coverage_end', 'synthetic')):
        raise ValueError('Comparison source coverage does not match.')
    periods, metrics = {}, {}
    for name in ('baseline', 'current'):
        periods[name] = _comparison_period(comparison[name]['period'])
        metrics[name] = {
            key: _comparison_int(comparison[name]['metrics'][key], nonnegative=key != 'net_revenue_cents')
            for key in COMPARISON_METRICS
        }
        if metrics[name]['net_revenue_cents'] != metrics[name]['gross_revenue_cents'] - metrics[name]['refund_cents']:
            raise ValueError('Comparison revenue does not reconcile to purchases and refunds.')
    if periods['current'] != _comparison_period(snapshot['period']):
        raise ValueError('Comparison current period does not match this snapshot.')
    if any(metrics['current'][key] != _comparison_int(snapshot['metrics'][key], nonnegative=key != 'net_revenue_cents')
           for key in COMPARISON_METRICS):
        raise ValueError('Comparison current totals do not match this snapshot.')
    before_start, before_end = (_comparison_day(periods['baseline'][key]) for key in ('start', 'end'))
    now_start, now_end = (_comparison_day(periods['current'][key]) for key in ('start', 'end'))
    days = (now_end - now_start).days + 1
    if (now_start - before_end).days != 1 or (before_end - before_start).days + 1 != days:
        raise ValueError('Comparison periods must be adjacent and equal in length.')
    if _comparison_int(comparison['days_per_period'], nonnegative=True) != days:
        raise ValueError('Comparison duration does not match its periods.')
    covered = before_start >= coverage_start and now_end <= coverage_end
    if type(comparison['fully_covered']) is not bool or comparison['fully_covered'] != covered:
        raise ValueError('Comparison coverage flag does not match its dates.')
    groups, seen = [], set()
    if not isinstance(comparison['groups'], list) or len(comparison['groups']) != len(COMPARISON_GROUPS):
        raise ValueError('Comparison must include all aggregate groups.')
    for row in comparison['groups']:
        identifier = row['id']
        if not isinstance(identifier, str) or identifier not in COMPARISON_GROUPS or identifier in seen:
            raise ValueError('Invalid comparison group.')
        seen.add(identifier)
        group = {'id': identifier, 'customers': _comparison_int(row['customers'], nonnegative=True)}
        group.update({key: _comparison_int(row[key]) for key in ('baseline_cents', 'current_cents', 'delta_cents')})
        if group['delta_cents'] != group['current_cents'] - group['baseline_cents']:
            raise ValueError('Comparison group change does not reconcile.')
        groups.append(group)
    groups.sort(key=lambda row: list(COMPARISON_GROUPS).index(row['id']))
    previous, current = metrics['baseline']['net_revenue_cents'], metrics['current']['net_revenue_cents']
    delta = current - previous
    if (sum(row['baseline_cents'] for row in groups) != previous
            or sum(row['current_cents'] for row in groups) != current
            or sum(row['delta_cents'] for row in groups) != delta
            or _comparison_int(comparison['delta_cents']) != delta
            or _comparison_int(comparison['reconciled_delta_cents']) != delta):
        raise ValueError('Comparison aggregate contributions do not reconcile.')
    limitations = [
        'These are observed purchases and refund timing, not evidence of what caused customer behavior.',
        'First observed does not prove a customer is new to the business.',
        'Comparison groups use the two periods and differ from lifetime recency segments.',
        'Net revenue is not profit; refunds can relate to purchases before the reporting period.',
    ]
    if not covered:
        limitations.append('At least one period extends beyond supplied coverage. Empty dates are unknown; rate and percentage comparisons are unavailable.')
    if previous <= 0:
        limitations.append('Percentage change is undefined because baseline net revenue is zero or negative.')
    return {
        'available': True,
        'baseline': {'period': periods['baseline'], 'metrics': metrics['baseline']},
        'current': {'period': periods['current'], 'metrics': metrics['current']},
        'delta_cents': delta, 'reconciled_delta_cents': sum(row['delta_cents'] for row in groups),
        'groups': groups, 'fully_covered': covered, 'days_per_period': days,
        'change_fraction': delta / previous if previous > 0 and covered else None,
        'percentage_available': previous > 0 and covered,
        'rate_comparison_available': covered,
        'coverage': {'start': coverage_start.isoformat(), 'end': coverage_end.isoformat(),
                     'synthetic': source['synthetic']},
        'limitations': limitations,
    }


def attach_comparison(snapshot: dict, data: Dataset) -> dict:
    """Bind the same dataset and dates without mutating the caller's snapshot."""
    period = _comparison_period(snapshot['period'])
    begin, finish = (_comparison_day(period[key]) for key in ('start', 'end'))
    days = (finish - begin).days + 1
    result = dict(snapshot)
    result.pop('comparison', None)
    result.pop('comparison_unavailable_reason', None)
    if begin - timedelta(days=days) < date(2000, 1, 1):
        current = build_dashboard(begin, finish, data)
        if (current['source']['revision'] != snapshot['source'].get('revision')
                or current['period'] != period or current['metrics'] != snapshot['metrics']):
            raise ValueError('Comparison dataset does not match this snapshot.')
        result['comparison_unavailable_reason'] = PRIOR_PERIOD_UNAVAILABLE
        return result
    result['comparison'] = compare_periods(data, begin, finish)
    aggregate_comparison(result)
    return result


def _comparison_money(cents: int) -> str:
    whole, fractional = divmod(abs(cents), 100)
    return ('-' if cents < 0 else '') + '$' + f'{whole:,}.{fractional:02d}'


def describe_change(snapshot: dict) -> dict:
    """Describe aggregate changes without a model or causal conclusions."""
    evidence = aggregate_comparison(snapshot)
    limitations = [*evidence['limitations'], *snapshot['source'].get('limitations', [])]
    base = {'provider': 'deterministic', 'source': snapshot['source'],
            'period': snapshot['period'], 'evidence': [evidence], 'limitations': limitations}
    if not evidence['available']:
        return {**base, 'status': 'clarification', 'answer': evidence['limitations'][0]}
    before, now = evidence['baseline'], evidence['current']
    answer = (
        f"Observed net revenue was {_comparison_money(now['metrics']['net_revenue_cents'])} "
        f"for {now['period']['start']} through {now['period']['end']}, compared with "
        f"{_comparison_money(before['metrics']['net_revenue_cents'])} for "
        f"{before['period']['start']} through {before['period']['end']}. "
        f"The recorded change is {_comparison_money(evidence['delta_cents'])}."
    )
    if evidence['percentage_available']:
        answer += f" That is {evidence['change_fraction'] * 100:.1f}% of the positive baseline."
    elif not evidence['fully_covered']:
        answer = ("Coverage is incomplete; unknown dates cannot establish a business-wide decline or growth. "
                  "No rate or percentage comparison is available. " + answer)
    else:
        answer += " Percentage change is undefined for a zero or negative baseline."
    contributions = sorted((row for row in evidence['groups'] if row['delta_cents']),
                           key=lambda row: (-abs(row['delta_cents']), row['id']))[:2]
    if contributions:
        answer += " Largest aggregate contributions: " + "; ".join(
            f"{COMPARISON_GROUPS[row['id']]} {_comparison_money(row['delta_cents'])}"
            for row in contributions) + "."
    answer += " These groups explain the arithmetic, not why customer behavior changed."
    return {**base, 'status': 'answered', 'answer': answer}
