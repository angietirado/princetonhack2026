import pytest
from spending_api.merchant import build_dashboard
from spending_api.merchant_experiments import plan_experiment


def plan(**kwargs):
    return plan_experiment(build_dashboard(), **{'segment':'slipping','max_customers':20,'offer_cents':250,'window_days':14, **kwargs})


def test_budget_is_a_ceiling_not_an_uplift_prediction():
    result = plan(budget_cents=2000)
    assert result['maximum_offer_cost_cents'] == 2500
    assert result['within_budget'] is False
    assert result['feasible_pilot'] is False
    assert result['treatment_customers'] == result['holdout_customers'] == 10
    assert result['proposed_audience_cap'] == 20
    assert 'forecast' in ' '.join(result['limitations'])


def test_audience_cap_and_small_samples():
    result = plan(max_customers=3, budget_cents=1000)
    assert result['treatment_customers'] == 1
    assert result['holdout_customers'] == 2
    assert result['within_budget'] is True
    assert result['feasible_pilot'] is False
    full = plan(max_customers=1000)
    assert full['proposed_audience_cap'] == full['observed_segment_customers']


@pytest.mark.parametrize('kwargs',[{'offer_cents':-1},{'offer_cents':2.5},{'max_customers':True}, {'window_days':2}, {'budget_cents':-1},{'segment':'all'}])
def test_invalid_inputs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        plan(**kwargs)


def test_planner_rejects_unobserved_future_cutoff():
    from datetime import date
    snapshot = build_dashboard(date(2026,11,15),date(2026,12,14))
    with pytest.raises(ValueError, match='within observed coverage'):
        plan_experiment(snapshot, segment='slipping', max_customers=20, offer_cents=250, window_days=14)
