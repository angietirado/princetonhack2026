"""HTTP boundaries for durable, source-bound draft pilots; no external calls."""
from copy import deepcopy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from spending_api.merchant_import import DatasetStore, parse_csv
from spending_api.merchant_main import create_app
from spending_api.merchant_pilots import PilotStorageError, PilotStore

PREFIX = '/api/v1/owner'
HEADERS = {'Authorization': 'Bearer owner-token', 'Origin': 'http://testserver'}
ASSUMPTIONS = {'segment': 'slipping', 'max_customers': 20, 'offer_cents': 500,
               'window_days': 14, 'budget_cents': 1000}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv('OWNER_DATA_DIR', raising=False)
    monkeypatch.setenv('OWNER_AI_ENABLED', 'false')


def review(client, **changes):
    response = client.post(PREFIX + '/experiments/plan', json={**ASSUMPTIONS, **changes}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def request_for(plan, **changes):
    return {'expected_dataset_id': plan['dataset_id'],
            'expected_revision': plan['source']['revision'],
            'reviewed_plan_hash': plan['reviewed_plan_hash'],
            'title': 'Bring quiet regulars back',
            'hypothesis': 'A small offer might increase return visits.',
            'measure': 'Compare return visit shares against a holdout.',
            'inputs': plan['inputs'], **changes}


def save(client, request):
    response = client.post(PREFIX + '/pilots', json=request, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def add_other_dataset(tmp_path):
    raw = ('kind,id,customer_id,order_id,date,amount_cents,status\n'
           'order,o1,c1,,2026-11-01,1000,completed\n')
    data, preview = parse_csv(raw, business_name='Other source', synthetic=False,
                             coverage_start='2026-09-01', coverage_end='2026-11-14')
    return DatasetStore(tmp_path).add(data, preview['preview_hash'], select=False)


def test_review_explicit_normalized_inputs_hash_and_nullable_budget():
    with TestClient(create_app(token='owner-token')) as client:
        plan = review(client, budget_cents=None)
        assert plan['dataset_id'] == plan['source']['dataset_id'] == 'demo'
        assert len(plan['reviewed_plan_hash']) == 64
        assert plan['inputs'] == {**ASSUMPTIONS, 'budget_cents': None, **plan['period']}
        assert plan['reviewed_plan_hash'] == review(client, budget_cents=None)['reviewed_plan_hash']
        assert plan['reviewed_plan_hash'] != review(client, budget_cents=0)['reviewed_plan_hash']
        assert plan['budget_cents'] is None


def test_save_restart_exact_retry_after_selection_and_observations(tmp_path, monkeypatch):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        assert client.get(PREFIX + '/status', headers=HEADERS).json()['can_save_pilots'] is True
        plan = review(client)
        request = request_for(plan)
        record = save(client, request)
        assert record['status'] == 'saved_draft'
        assert record['inputs'] == plan['inputs']
        assert record['source'] == plan['source']
        assert record['reviewed_plan_hash'] == plan['reviewed_plan_hash']
        assert record['calculation']['feasible_pilot'] is False
        assert record['calculation']['within_budget'] is False
        assert record['calculation']['proposed_audience_cap'] == 20
        assert record['calculation']['limitations'] == plan['limitations']
        assert record['notes'] == []
        assert not {'customers', 'assignments', 'computed_effects'} & set(record)
        other = add_other_dataset(tmp_path)
        DatasetStore(tmp_path).select(other)
        assert save(client, request) == record
        note_body = {'observed_on': '2026-11-14', 'text': 'No pilot was launched; I am reviewing the budget.'}
        note = client.post(PREFIX + '/pilots/' + record['id'] + '/notes', json=note_body, headers=HEADERS)
        assert note.status_code == 200, note.text
        note = note.json()
        assert note['verification'] == 'owner_reported'
        assert note['pilot_id'] == record['id']
        repeated = client.post(PREFIX + '/pilots/' + record['id'] + '/notes', json=note_body, headers=HEADERS)
        assert repeated.json() == note
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as restarted:
        def must_not_read_active(_self):
            raise AssertionError('Saved records must not bind to the active dataset')
        monkeypatch.setattr(DatasetStore, 'active', must_not_read_active)
        loaded = restarted.get(PREFIX + '/pilots/' + record['id'], headers=HEADERS)
        assert loaded.status_code == 200
        assert loaded.json() == {**record, 'notes': [note]}
        assert save(restarted, request) == loaded.json()
        listing = restarted.get(PREFIX + '/pilots?dataset_id=demo', headers=HEADERS).json()
        assert listing['pagination']['total'] == 1
        assert listing['pilots'][0]['note_count'] == 1
        assert listing['pilots'][0]['source']['dataset_id'] == 'demo'
        assert restarted.get(PREFIX + '/pilots?dataset_id=' + other, headers=HEADERS).json()['pilots'] == []


def test_save_captures_dataset_once_during_concurrent_selection(tmp_path, monkeypatch):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        request = request_for(review(client, budget_cents=None))
        other = add_other_dataset(tmp_path)
        active = DatasetStore.active
        reads = []
        def switch_after_capture(store):
            captured = active(store)
            reads.append(captured[0])
            store.select(other)
            return captured
        monkeypatch.setattr(DatasetStore, 'active', switch_after_capture)
        record = save(client, request)
        assert reads == ['demo']
        assert record['source']['dataset_id'] == 'demo'
        assert record['inputs']['budget_cents'] is None
        assert record['calculation']['budget_cents'] is None
        assert active(DatasetStore(tmp_path))[0] == other


@pytest.mark.parametrize('change', ['revision', 'hash', 'input', 'selection'])
def test_stale_or_tampered_review_fails_without_saved_record(tmp_path, change):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        request = request_for(review(client))
        if change == 'revision':
            request['expected_revision'] = 'f' * 64
        elif change == 'hash':
            request['reviewed_plan_hash'] = 'f' * 64
        elif change == 'input':
            request['inputs']['offer_cents'] += 1
        else:
            DatasetStore(tmp_path).select(add_other_dataset(tmp_path))
        response = client.post(PREFIX + '/pilots', json=request, headers=HEADERS)
        assert response.status_code == 409, response.text
        assert client.get(PREFIX + '/pilots', headers=HEADERS).json()['pagination']['total'] == 0


def test_pagination_filter_and_different_notes_remain_append_only(tmp_path):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        request = request_for(review(client))
        first = save(client, request)
        second = save(client, {**request, 'title': 'Another reviewed pilot'})
        page = client.get(PREFIX + '/pilots?limit=1', headers=HEADERS).json()
        assert page['pagination'] == {'offset': 0, 'limit': 1, 'total': 2, 'next_offset': 1}
        next_page = client.get(PREFIX + '/pilots?limit=1&offset=1', headers=HEADERS).json()
        assert {page['pilots'][0]['id'], next_page['pilots'][0]['id']} == {first['id'], second['id']}
        assert next_page['pagination']['next_offset'] is None
        for day in ('2026-11-14', '2026-11-15'):
            result = client.post(PREFIX + '/pilots/' + first['id'] + '/notes',
                                 json={'observed_on': day, 'text': 'Owner observation'}, headers=HEADERS)
            assert result.status_code == 200, result.text
        detail = client.get(PREFIX + '/pilots/' + first['id'], headers=HEADERS).json()
        assert len(detail['notes']) == 2
        assert {note['observed_on'] for note in detail['notes']} == {'2026-11-14', '2026-11-15'}
        assert {key: value for key, value in detail.items() if key != 'notes'} == {key: value for key, value in first.items() if key != 'notes'}
        for method in (client.put, client.delete):
            assert method(PREFIX + '/pilots/' + first['id'], headers=HEADERS).status_code == 405


def test_without_storage_reports_capability_and_disables_saved_routes():
    with TestClient(create_app(token='owner-token')) as client:
        assert client.get(PREFIX + '/status', headers=HEADERS).json()['can_save_pilots'] is False
        request = request_for(review(client))
        assert client.post(PREFIX + '/pilots', json=request, headers=HEADERS).status_code == 503
        assert client.get(PREFIX + '/pilots', headers=HEADERS).status_code == 503
        assert client.get(PREFIX + '/pilots/' + 'a' * 64, headers=HEADERS).status_code == 503
        assert client.post(PREFIX + '/pilots/' + 'a' * 64 + '/notes',
                           json={'observed_on': '2026-11-14', 'text': 'Note'}, headers=HEADERS).status_code == 503


def test_auth_origin_media_size_duplicate_json_and_no_client_idempotency(tmp_path):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        request = request_for(review(client))
        for path in ('/pilots', '/pilots/' + 'a' * 64):
            assert client.get(PREFIX + path).status_code == 401
        for path in ('/pilots', '/pilots/' + 'a' * 64 + '/notes'):
            assert client.post(PREFIX + path, json={}).status_code == 401
        assert client.post(PREFIX + '/pilots', json=request,
                           headers={**HEADERS, 'Origin': 'https://other.example'}).status_code == 403
        assert client.post(PREFIX + '/pilots', content='{}', headers=HEADERS).status_code == 415
        assert client.post(PREFIX + '/pilots', json={**request, 'idempotency_key': 'client-key'}, headers=HEADERS).status_code == 422
        assert client.post(PREFIX + '/pilots?title=override', json=request, headers=HEADERS).status_code == 422
        raw = json.dumps(request).replace('"budget_cents": 1000', '"budget_cents": 1000, "budget_cents": 0')
        assert client.post(PREFIX + '/pilots', content=raw,
                           headers={**HEADERS, 'Content-Type': 'application/json'}).status_code == 422
        assert client.post(PREFIX + '/pilots', content=' ' * 24_577,
                           headers={**HEADERS, 'Content-Type': 'application/json'}).status_code == 413
        assert client.get(PREFIX + '/pilots', headers=HEADERS).json()['pagination']['total'] == 0


@pytest.mark.parametrize('field,value', [
    ('budget_cents', True), ('offer_cents', 1.5), ('max_customers', '20'),
    ('start', '20261101'), ('end', '2026-11-14T00:00:00'), ('start', 0),
    ('end', None), ('segment', 'everybody'), ('window_days', 91),
])
def test_save_rejects_ambiguous_or_invalid_inputs(tmp_path, field, value):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        request = request_for(review(client))
        request['inputs'][field] = value
        assert client.post(PREFIX + '/pilots', json=request, headers=HEADERS).status_code == 422


def test_save_requires_explicit_budget_dates_and_nonblank_description(tmp_path):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        request = request_for(review(client))
        for field in ('budget_cents', 'start', 'end'):
            incomplete = deepcopy(request)
            incomplete['inputs'].pop(field)
            assert client.post(PREFIX + '/pilots', json=incomplete, headers=HEADERS).status_code == 422
        for change in ({'title': ' '}, {'title': 'x' * 121}, {'hypothesis': ''}, {'measure': 'x' * 2001}):
            assert client.post(PREFIX + '/pilots', json={**request, **change}, headers=HEADERS).status_code == 422


def test_note_validation_absent_ids_and_query_bounds(tmp_path):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        record = save(client, request_for(review(client)))
        note = {'observed_on': '2026-11-14', 'text': 'Observed by owner'}
        for change in ({'observed_on': '20261114'}, {'observed_on': '1999-12-31'},
                       {'observed_on': '2026-02-30'}, {'observed_on': 0},
                       {'text': ' '}, {'text': 'x' * 2001}, {'effect': 1}):
            response = client.post(PREFIX + '/pilots/' + record['id'] + '/notes', json={**note, **change}, headers=HEADERS)
            assert response.status_code == 422, response.text
        assert client.get(PREFIX + '/pilots/' + 'f' * 64, headers=HEADERS).status_code == 404
        assert client.post(PREFIX + '/pilots/' + 'f' * 64 + '/notes', json=note, headers=HEADERS).status_code == 404
        assert client.get(PREFIX + '/pilots/not-a-hash', headers=HEADERS).status_code == 422
        for query in ('limit=0', 'limit=51', 'offset=-1', 'offset=1001', 'dataset_id=arbitrary'):
            assert client.get(PREFIX + '/pilots?' + query, headers=HEADERS).status_code == 422
        assert client.get(PREFIX + '/pilots/' + record['id'], headers=HEADERS).json()['notes'] == []


@pytest.mark.parametrize('error', [sqlite3.OperationalError, OSError, PilotStorageError])
def test_storage_errors_are_sanitized_for_every_route(tmp_path, monkeypatch, error):
    with TestClient(create_app(token='owner-token', data_dir=tmp_path)) as client:
        request = request_for(review(client))
        def fail(*args, **kwargs):
            raise error('private-path-or-payload-do-not-return')
        for method in ('save', 'get', 'list', 'append_note'):
            monkeypatch.setattr(PilotStore, method, fail)
        responses = [
            client.post(PREFIX + '/pilots', json=request, headers=HEADERS),
            client.get(PREFIX + '/pilots', headers=HEADERS),
            client.get(PREFIX + '/pilots/' + 'a' * 64, headers=HEADERS),
            client.post(PREFIX + '/pilots/' + 'a' * 64 + '/notes',
                        json={'observed_on': '2026-11-14', 'text': 'Owner note'}, headers=HEADERS),
        ]
        for response in responses:
            assert response.status_code == 503
            assert 'private-path' not in response.text
            assert response.headers['Cache-Control'] == 'no-store'
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
