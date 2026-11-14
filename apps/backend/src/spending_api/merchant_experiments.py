"""A deterministic pilot proposal, without assignment, outreach or predicted uplift."""
from __future__ import annotations


def plan_experiment(snapshot: dict, *, segment: str, max_customers: int,
                    offer_cents: int, window_days: int, budget_cents: int | None = None) -> dict:
    if snapshot['period']['end'] > snapshot['source']['coverage_end']:
        raise ValueError('Choose an end date within observed coverage before planning a pilot; later inactivity is unknown.')
    if segment not in {'champions', 'regulars', 'promising', 'slipping', 'new'}:
        raise ValueError('Choose a known customer group.')
    for value, low, high, label in ((max_customers, 1, 1000, 'Audience cap'),
                                    (offer_cents, 0, 5000, 'Offer cents'),
                                    (window_days, 7, 90, 'Evaluation days')):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{label} must be a whole number between {low} and {high}.')
    if budget_cents is not None and (type(budget_cents) is not int or not 0 <= budget_cents <= 5_000_000):
        raise ValueError('Budget must be whole cents between 0 and 5000000.')
    row = next((row for row in snapshot['segments'] if row['id'] == segment), None)
    observed = row['customers'] if row else 0
    audience = min(max_customers, observed)
    treatment = audience // 2
    holdout = audience - treatment
    maximum = treatment * offer_cents
    fits = budget_cents is None or maximum <= budget_cents
    return {
        'status': 'ok', 'segment': segment, 'observed_segment_customers': observed,
        'proposed_audience_cap': audience, 'offer_cents': offer_cents,
        'treatment_customers': treatment, 'holdout_customers': holdout,
        'maximum_offer_cost_cents': maximum, 'window_days': window_days,
        'budget_cents': budget_cents, 'within_budget': fits,
        'feasible_pilot': audience >= 4 and fits,
        'source_revision': snapshot['source']['revision'], 'period': snapshot['period'],
        'method': 'Owner reviews eligibility and consent, then randomly assigns the capped audience to offer and holdout groups of approximately equal size.',
        'success_metric': 'Compare the share with a completed return visit within the same evaluation window; the denominator is all assigned customers in each group.',
        'limitations': [
            'This is a draft only. No customer is assigned or contacted.',
            'Maximum offer cost assumes one full-value redemption per treatment customer. It excludes delivery, labor, product and setup costs.',
            'No effect size, causal result, revenue forecast or statistical power is established.',
            'At least four observed customers are needed for this illustrative split; a small pilot may still be inconclusive.',
            'Changing the budget does not silently change the audience or offer. Revise explicit inputs if the proposal exceeds the budget.',
        ],
    }
