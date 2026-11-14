from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import json
from threading import Barrier

import pytest

from spending_api.merchant import DEMO, build_dashboard
from spending_api.merchant_experiments import plan_experiment
from spending_api.merchant_import import DatasetStore, parse_csv
from spending_api import merchant_pilots
from spending_api.merchant_pilots import (
    PilotConflict, PilotLimitExceeded, PilotNotFound, PilotStorageError,
    PilotStore, review_plan,
)


def snapshot(data=DEMO, key='demo', start=None, end=None):
    result = build_dashboard(start, end, data)
    result['source']['dataset_id'] = key
    return result


def review(data=None, **overrides):
    return review_plan(data or snapshot(), **{
        'segment': 'slipping', 'max_customers': 1000, 'offer_cents': 250,
        'window_days': 14, 'budget_cents': None, **overrides})


def save_request(data=None, **prose):
    planned = review(data)
    return {
        'expected_dataset_id': planned['dataset_id'],
        'expected_revision': planned['source_revision'],
        'reviewed_plan_hash': planned['reviewed_plan_hash'],
        'title': 'Invite quiet regulars back',
        'hypothesis': 'A small invitation may encourage another visit.',
        'measure': 'Observe return visits over the same evaluation period.',
        'inputs': planned['inputs'], **prose,
    }


def bound(data):
    def load(start, end):
        assert (start, end) == (data['period']['start'], data['period']['end'])
        return data
    return load


def forbidden_snapshot(*_):
    raise AssertionError('An exact retry must not load the active dataset')


@pytest.fixture
def store(tmp_path):
    return PilotStore(DatasetStore(tmp_path))


def test_review_preserves_shared_calculation_requested_cap_and_nullable_budget():
    data = snapshot()
    reviewed = review(data)
    original = plan_experiment(data, segment='slipping', max_customers=1000,
                               offer_cents=250, window_days=14, budget_cents=None)
    assert {key: reviewed[key] for key in original} == original
    assert reviewed['inputs']['max_customers'] == 1000
    assert reviewed['proposed_audience_cap'] < 1000
    assert reviewed['inputs']['budget_cents'] is None
    assert reviewed['inputs']['start'] == data['period']['start']
    assert reviewed['inputs']['end'] == data['period']['end']
    assert reviewed['dataset_id'] == 'demo'
    assert reviewed['reviewed_plan_hash'] == review(data)['reviewed_plan_hash']
    assert len(reviewed['reviewed_plan_hash']) == 64
    assert not {'customers', 'orders', 'refunds', 'cohorts'} & reviewed.keys()
    reviewed['source']['limitations'].append('Changed by caller')
    assert 'Changed by caller' not in data['source']['limitations']


def test_review_hash_binds_requested_inputs_source_and_calculation():
    data = snapshot()
    a = review(data)
    b = review(data, max_customers=999)
    assert a['proposed_audience_cap'] == b['proposed_audience_cap']
    assert a['reviewed_plan_hash'] != b['reviewed_plan_hash']
    changed = deepcopy(data)
    changed['source']['dataset_id'] = 'f' * 64
    assert review(changed)['reviewed_plan_hash'] != a['reviewed_plan_hash']
    changed = deepcopy(data)
    changed['source']['revision'] = 'e' * 64
    assert review(changed)['reviewed_plan_hash'] != a['reviewed_plan_hash']
    changed = deepcopy(data)
    next(row for row in changed['segments'] if row['id'] == 'slipping')['customers'] += 1
    assert review(changed)['reviewed_plan_hash'] != a['reviewed_plan_hash']


def test_save_is_durable_deduplicated_and_never_changes_dataset_selection(store, tmp_path):
    data = snapshot()
    request = save_request(data)
    before = store.datasets.list()
    first = store.save(request, bound(data))
    assert first['status'] == 'saved_draft'
    assert first['inputs']['max_customers'] == 1000
    assert first['calculation']['budget_cents'] is None
    assert first['business']['name'] == 'Harbor & Pine'
    assert first['notes'] == []
    assert first['created_at'].endswith('Z')
    assert store.datasets.active() == ('demo', None)
    assert store.datasets.list() == before
    reloaded = PilotStore(DatasetStore(tmp_path))
    assert reloaded.get(first['id']) == first
    padded = {**request, 'title': '  ' + request['title'] + '\n'}
    assert reloaded.save(padded, forbidden_snapshot) == first
    assert reloaded.list()['pagination']['total'] == 1
    assert all(customer['id'] not in json.dumps(first) for customer in data['customers'])


def test_existing_save_can_retry_after_active_selection_changes(store):
    data = snapshot()
    request = save_request(data)
    first = store.save(request, bound(data))
    csv = 'kind,id,customer_id,order_id,date,amount_cents,status\norder,o1,c1,,2026-11-01,1000,completed\n'
    imported, preview = parse_csv(csv, business_name='Another shop', synthetic=True)
    key = store.datasets.add(imported, preview['preview_hash'])
    assert store.save(request, forbidden_snapshot) == first
    assert store.get(first['id'])['source']['dataset_id'] == 'demo'
    assert store.datasets.active()[0] == key
    with pytest.raises(PilotConflict, match='dataset or period'):
        store.save({**request, 'hypothesis': 'A new hypothesis'},
                   lambda start, end: snapshot(imported, key, date.fromisoformat(start), date.fromisoformat(end)))


def test_changed_prose_creates_separate_immutable_draft(store):
    data = snapshot()
    first = store.save(save_request(data), bound(data))
    second = store.save(save_request(data, measure='Record what happened, with no causal claim.'), bound(data))
    assert first['id'] != second['id']
    assert store.get(first['id']) == first
    assert store.list()['pagination']['total'] == 2


@pytest.mark.parametrize('change', ['revision', 'dataset', 'period', 'review_hash', 'input'])
def test_unreviewed_or_mismatched_save_is_rejected_without_side_effect(store, change):
    data = snapshot()
    request = save_request(data)
    supplied = deepcopy(data)
    if change == 'revision':
        supplied['source']['revision'] = 'd' * 64
    elif change == 'dataset':
        supplied['source']['dataset_id'] = 'c' * 64
    elif change == 'period':
        supplied['period']['start'] = '2026-10-17'
    elif change == 'review_hash':
        request['reviewed_plan_hash'] = 'b' * 64
    else:
        request['inputs']['max_customers'] = 999
    with pytest.raises(PilotConflict):
        store.save(request, lambda *_: supplied)
    assert store.list()['pagination']['total'] == 0
    assert store.datasets.active() == ('demo', None)


def test_save_recomputes_using_shared_planner_and_detects_versioned_change(store, monkeypatch):
    data = snapshot()
    request = save_request(data)
    original = merchant_pilots.plan_experiment
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        result['maximum_offer_cost_cents'] += 1
        return result
    monkeypatch.setattr(merchant_pilots, 'plan_experiment', changed)
    with pytest.raises(PilotConflict, match='calculation changed'):
        store.save(request, bound(data))


def test_infeasible_and_budgetless_drafts_are_saved_without_approval_semantics(store):
    data = snapshot()
    planned = review(data, max_customers=3, budget_cents=0)
    request = {**save_request(data), 'inputs': planned['inputs'],
               'reviewed_plan_hash': planned['reviewed_plan_hash']}
    saved = store.save(request, bound(data))
    assert saved['calculation']['feasible_pilot'] is False
    assert saved['calculation']['within_budget'] is False
    assert saved['calculation']['proposed_audience_cap'] == 3
    assert saved['status'] == 'saved_draft'
    assert 'No customer is assigned or contacted' in ' '.join(saved['calculation']['limitations'])


def test_concurrent_exact_saves_keep_one_original_record(store):
    data = snapshot()
    request = save_request(data)
    barrier = Barrier(2)
    def load(*_):
        barrier.wait(timeout=10)
        return data
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.save(request, load), range(2)))
    assert results[0] == results[1]
    assert store.list()['pagination']['total'] == 1


def test_list_has_stable_ties_pagination_and_explicit_source_filter(store, monkeypatch):
    monkeypatch.setattr(merchant_pilots, '_now', lambda: '2026-11-14T12:00:00.000000Z')
    demo = snapshot()
    imported = deepcopy(demo)
    imported['source']['dataset_id'] = 'a' * 64
    records = [store.save(save_request(data, title=title), bound(data)) for data, title in
               [(demo, 'First'), (demo, 'Second'), (imported, 'Third')]]
    first = store.list(limit=2)
    second = store.list(limit=2, offset=first['pagination']['next_offset'])
    ids = [row['id'] for row in first['pilots'] + second['pilots']]
    assert ids == sorted((row['id'] for row in records), reverse=True)
    assert first['pagination'] == {'offset': 0, 'limit': 2, 'total': 3, 'next_offset': 2}
    assert second['pagination']['next_offset'] is None
    assert len(set(ids)) == 3
    filtered = store.list(dataset_id='demo')
    assert filtered['pagination']['total'] == 2
    assert all(row['source']['dataset_id'] == 'demo' for row in filtered['pilots'])
    assert store.list(offset=1000)['pilots'] == []


def test_store_limits_allow_exact_retries_at_capacity(store, monkeypatch):
    monkeypatch.setattr(merchant_pilots, 'MAX_PILOTS', 2)
    data = snapshot()
    first = store.save(save_request(data), bound(data))
    store.save(save_request(data, title='Another draft'), bound(data))
    with pytest.raises(PilotLimitExceeded):
        store.save(save_request(data, title='Over capacity'), bound(data))
    assert store.save(save_request(data), forbidden_snapshot) == first
    assert store.list()['pagination']['total'] == 2


def test_notes_append_deduplicate_survive_restart_and_do_not_change_plan(store, tmp_path):
    data = snapshot()
    draft = store.save(save_request(data), bound(data))
    note = store.append_note(draft['id'], observed_on='2026-11-14', text='  We discussed the idea; no trial was run.  ')
    assert note['verification'] == 'owner_reported'
    assert note['text'] == 'We discussed the idea; no trial was run.'
    assert store.append_note(draft['id'], observed_on='2026-11-14', text=note['text']) == note
    second = store.append_note(draft['id'], observed_on='2026-11-14', text='A second observation.')
    reloaded = PilotStore(DatasetStore(tmp_path)).get(draft['id'])
    assert reloaded['notes'] == [note, second]
    assert {key: value for key, value in reloaded.items() if key != 'notes'} == {key: value for key, value in draft.items() if key != 'notes'}
    assert store.list()['pilots'][0]['note_count'] == 2
    assert store.datasets.active() == ('demo', None)
    assert store.save(save_request(data), forbidden_snapshot)['notes'] == [note, second]


def test_note_limit_is_per_draft_and_deduplicated_retry_survives_limit(store):
    data = snapshot()
    draft = store.save(save_request(data), bound(data))
    first = None
    for index in range(50):
        note = store.append_note(draft['id'], observed_on='2026-11-14', text=f'Observation {index}')
        first = first or note
    with pytest.raises(PilotLimitExceeded):
        store.append_note(draft['id'], observed_on='2026-11-14', text='Observation 51')
    assert store.append_note(draft['id'], observed_on='2026-11-14', text='Observation 0') == first
    another = store.save(save_request(data, title='Separate draft'), bound(data))
    assert store.append_note(another['id'], observed_on='2026-11-14', text='Observation 0')['id'] != first['id']
    assert len(store.get(draft['id'])['notes']) == 50


@pytest.mark.parametrize('field,value', [('title', ''), ('title', 'x' * 121), ('hypothesis', '  '),
    ('measure', 'x' * 2001), ('expected_revision', 'bad'), ('expected_dataset_id', 'bad'),
    ('reviewed_plan_hash', None)])
def test_save_rejects_invalid_metadata_before_loading_data(store, field, value):
    with pytest.raises(ValueError):
        store.save({**save_request(), field: value}, forbidden_snapshot)


@pytest.mark.parametrize('field,value', [('max_customers', True), ('max_customers', '20'),
    ('offer_cents', 1.5), ('budget_cents', False), ('budget_cents', -1), ('window_days', 6),
    ('segment', 'all'), ('start', None), ('start', 0), ('start', '2026-11-01T00:00:00Z'),
    ('start', '2026-02-30'), ('start', '1999-12-31'), ('end', '2101-01-01'), ('end', '2026-01-01')])
def test_save_rejects_invalid_explicit_inputs(store, field, value):
    request = save_request()
    request['inputs'][field] = value
    with pytest.raises(ValueError):
        store.save(request, forbidden_snapshot)


def test_save_rejects_missing_or_extra_fields(store):
    request = save_request()
    request['inputs'].pop('budget_cents')
    with pytest.raises(ValueError):
        store.save(request, forbidden_snapshot)
    with pytest.raises(ValueError):
        store.save({**save_request(), 'customer_ids': ['c1']}, forbidden_snapshot)
    with pytest.raises(ValueError):
        store.save({**save_request(), 'idempotency_key': 'not-supported'}, forbidden_snapshot)


@pytest.mark.parametrize('options', [{'limit': True}, {'limit': 0}, {'limit': 51},
    {'offset': -1}, {'offset': 1001}, {'offset': 1.0}, {'dataset_id': 'other'}])
def test_list_bounds_are_strict(store, options):
    with pytest.raises(ValueError):
        store.list(**options)


@pytest.mark.parametrize('day,text', [('20261114', 'note'), ('2026-02-30', 'note'),
    ('2101-01-01', 'note'), ('2026-11-14', ''), ('2026-11-14', 'x' * 2001)])
def test_invalid_note_is_rejected_without_mutation(store, day, text):
    data = snapshot()
    draft = store.save(save_request(data), bound(data))
    with pytest.raises(ValueError):
        store.append_note(draft['id'], observed_on=day, text=text)
    assert store.get(draft['id'])['notes'] == []


def test_unknown_records_do_not_create_orphan_notes(store):
    with pytest.raises(PilotNotFound):
        store.get('0' * 64)
    with pytest.raises(PilotNotFound):
        store.append_note('0' * 64, observed_on='2026-11-14', text='An owner note')
    with store.datasets.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM pilot_notes').fetchone()[0] == 0


def test_corrupt_persisted_record_reports_storage_error(store):
    data = snapshot()
    draft = store.save(save_request(data), bound(data))
    with store.datasets.connect() as db:
        db.execute('UPDATE pilot_records SET payload=? WHERE id=?', ('{bad', draft['id']))
    with pytest.raises(PilotStorageError):
        store.get(draft['id'])
    with pytest.raises(PilotStorageError):
        store.list()

def test_concurrent_identical_notes_keep_one_original_timestamp(store):
    data = snapshot()
    draft = store.save(save_request(data), bound(data))
    barrier = Barrier(2)
    def append(_):
        barrier.wait(timeout=10)
        return store.append_note(draft['id'], observed_on='2026-11-14', text='The owner discussed the draft.')
    with ThreadPoolExecutor(max_workers=2) as pool:
        notes = list(pool.map(append, range(2)))
    assert notes[0] == notes[1]
    assert store.get(draft['id'])['notes'] == [notes[0]]


def test_concurrent_distinct_notes_cannot_exceed_capacity(store, monkeypatch):
    monkeypatch.setattr(merchant_pilots, 'MAX_NOTES', 1)
    data = snapshot()
    draft = store.save(save_request(data), bound(data))
    barrier = Barrier(2)
    def append(index):
        barrier.wait(timeout=10)
        try:
            return store.append_note(draft['id'], observed_on='2026-11-14', text=f'Observation {index}')
        except PilotLimitExceeded:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        notes = list(pool.map(append, range(2)))
    assert sum(note is not None for note in notes) == 1
    assert len(store.get(draft['id'])['notes']) == 1


def test_review_rejects_future_cutoff_without_observed_coverage():
    with pytest.raises(ValueError, match='within observed coverage'):
        review(snapshot(end=date(2026, 12, 1)))

def test_saved_evidence_remains_readable_after_calculator_version_changes(store, monkeypatch):
    data = snapshot()
    request = save_request(data)
    original = store.save(request, bound(data))
    monkeypatch.setattr(merchant_pilots, 'CALCULATOR_VERSION', 2)
    assert store.get(original['id']) == original
    assert store.save(request, forbidden_snapshot) == original
    assert store.list()['pilots'][0]['id'] == original['id']
    with pytest.raises(PilotConflict, match='calculation changed'):
        store.save({**request, 'title': 'An unreviewed new draft'}, bound(data))