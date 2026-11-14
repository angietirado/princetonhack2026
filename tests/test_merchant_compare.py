from datetime import date
from spending_api.merchant import Dataset, Order, Refund, DEMO
from spending_api.merchant_compare import compare_periods


def test_compare_groups_reconcile_with_refunds_and_first_seen_history():
    data = Dataset((
        Order('o1','regular',date(2026,10,1),1000),
        Order('o2','regular',date(2026,10,8),1500),
        Order('o3','quiet',date(2026,10,2),2000),
        Order('o4','new',date(2026,10,9),3000),
        Order('o5','reactivated',date(2026,9,1),1000),
        Order('o6','reactivated',date(2026,10,10),2000),
    ), (Refund('r1','o3',date(2026,10,9),500),),
        coverage_start=date(2026,9,1), coverage_end=date(2026,10,14), as_of=date(2026,10,15))
    result = compare_periods(data,date(2026,10,8),date(2026,10,14))
    assert result['baseline']['metrics']['net_revenue_cents'] == 3000
    assert result['current']['metrics']['net_revenue_cents'] == 6000
    assert result['delta_cents'] == 3000
    assert sum(row['delta_cents'] for row in result['groups']) == 3000
    groups = {row['id']:row for row in result['groups']}
    assert groups['first_observed']['current_cents'] == 3000
    assert groups['returned']['current_cents'] == 2000
    assert groups['went_quiet']['delta_cents'] == -2500
    assert result['change_fraction'] == 1


def test_missing_prior_coverage_has_no_percentage_and_reconciles():
    result = compare_periods(DEMO,DEMO.coverage_start,DEMO.coverage_start)
    assert result['fully_covered'] is False
    assert result['change_fraction'] is None
    assert result['baseline']['metrics']['net_revenue_cents'] == 0
    assert result['reconciled_delta_cents'] == result['delta_cents']


def test_truncated_contributors_explicitly_reconcile():
    result = compare_periods(DEMO)
    assert len(result['contributors']) <= 20
    assert result['omitted_contributors'] > 0
    assert sum(row['delta_cents'] for row in result['contributors']) + result['omitted_delta_cents'] == result['delta_cents']
