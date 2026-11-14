"""Immutable, reviewed pilot drafts and append-only owner observations.

Only aggregate calculations are retained. Saving does not assign, contact, or
record participation by any customer. The existing dataset database and
selection remain intact; these tables are additive.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Any

from spending_api.merchant_experiments import plan_experiment
from spending_api.merchant_import import DatasetStore

SCHEMA_VERSION = 1
CALCULATOR_VERSION = 1
MAX_PILOTS = 1000
MAX_NOTES = 50
SEGMENTS = frozenset({'champions', 'regulars', 'promising', 'slipping', 'new'})
INPUT_FIELDS = frozenset({'segment', 'max_customers', 'offer_cents', 'window_days',
                          'budget_cents', 'start', 'end'})
SAVE_FIELDS = frozenset({'expected_dataset_id', 'expected_revision', 'reviewed_plan_hash',
                         'title', 'hypothesis', 'measure', 'inputs'})
SOURCE_FIELDS = ('dataset_id', 'revision', 'label', 'synthetic', 'coverage_start',
                 'coverage_end', 'as_of', 'segment_as_of', 'limitations')
REVIEW_EXTRAS = frozenset({'source', 'inputs', 'dataset_id', 'reviewed_plan_hash'})


class PilotNotFound(LookupError):
    """The requested saved draft does not exist."""


class PilotConflict(ValueError):
    """The calculation or source differs from the owner's reviewed draft."""


class PilotStorageError(RuntimeError):
    """Persisted records are malformed or use an unsupported schema."""


class PilotLimitExceeded(ValueError):
    """This local store has reached a bounded record or observation limit."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError(f'{label} must be a SHA-256 digest.')
    return value


def _dataset_id(value: Any) -> str:
    return value if value == 'demo' else _hash(value, 'Dataset ID')


def _text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError(f'{label} must contain 1 to {maximum} characters.')
    return value.strip()


def _day(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Use an explicit ISO calendar date YYYY-MM-DD.')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError('Use a valid ISO calendar date YYYY-MM-DD.') from None
    if not date(2000, 1, 1) <= parsed <= date(2100, 12, 31):
        raise ValueError('Supported dates are 2000-01-01 through 2100-12-31.')
    return parsed.isoformat()


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be a whole number from {minimum} to {maximum}.')
    return value


def _fields(value: Any, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f'{label} must contain exactly the documented fields.')
    return dict(value)


def _inputs(value: Any) -> dict[str, Any]:
    inputs = _fields(value, INPUT_FIELDS, 'Pilot inputs')
    segment = inputs['segment']
    if not isinstance(segment, str) or segment not in SEGMENTS:
        raise ValueError('Choose a known customer group.')
    inputs['max_customers'] = _integer(inputs['max_customers'], 'Audience cap', 1, 1000)
    inputs['offer_cents'] = _integer(inputs['offer_cents'], 'Offer cents', 0, 5000)
    inputs['window_days'] = _integer(inputs['window_days'], 'Evaluation days', 7, 90)
    if inputs['budget_cents'] is not None:
        inputs['budget_cents'] = _integer(inputs['budget_cents'], 'Budget cents', 0, 5_000_000)
    inputs['start'], inputs['end'] = _day(inputs['start']), _day(inputs['end'])
    if inputs['start'] > inputs['end']:
        raise ValueError('Choose an end date on or after the start date.')
    return inputs


def _request(value: Any) -> dict[str, Any]:
    request = _fields(value, SAVE_FIELDS, 'Save request')
    request['expected_dataset_id'] = _dataset_id(request['expected_dataset_id'])
    request['expected_revision'] = _hash(request['expected_revision'], 'Expected revision')
    request['reviewed_plan_hash'] = _hash(request['reviewed_plan_hash'], 'Reviewed plan hash')
    for key, maximum in (('title', 120), ('hypothesis', 2000), ('measure', 2000)):
        request[key] = _text(request[key], key.capitalize(), maximum)
    request['inputs'] = _inputs(request['inputs'])
    return {'schema_version': SCHEMA_VERSION, **request}


def _source(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    source = {key: deepcopy(snapshot['source'][key]) for key in SOURCE_FIELDS}
    source['dataset_id'] = _dataset_id(source['dataset_id'])
    source['revision'] = _hash(source['revision'], 'Source revision')
    if type(source['synthetic']) is not bool:
        raise ValueError('Source synthetic status must be explicit.')
    return source


def review_plan(snapshot: dict[str, Any], *, segment: str, max_customers: int,
                offer_cents: int, window_days: int,
                budget_cents: int | None = None) -> dict[str, Any]:
    """Calculate a source-bound review and retain the requested audience cap."""
    inputs = _inputs({'segment': segment, 'max_customers': max_customers,
                      'offer_cents': offer_cents, 'window_days': window_days,
                      'budget_cents': budget_cents, **snapshot['period']})
    source = _source(snapshot)
    calculation = plan_experiment(snapshot, **{key: inputs[key] for key in
        ('segment', 'max_customers', 'offer_cents', 'window_days', 'budget_cents')})
    review_hash = _digest({'schema_version': SCHEMA_VERSION,
                          'calculator_version': CALCULATOR_VERSION,
                          'source': source, 'period': snapshot['period'],
                          'inputs': inputs, 'calculation': calculation})
    return {**calculation, 'source': source, 'inputs': inputs,
            'dataset_id': source['dataset_id'], 'reviewed_plan_hash': review_hash}


def _stored(raw: str, *, note: bool = False) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        required = ({'schema_version', 'pilot_id', 'observed_on', 'text', 'verification'}
                    if note else {'schema_version', 'calculator_version', 'status', 'title',
                                  'hypothesis', 'measure', 'business', 'source', 'period',
                                  'inputs', 'reviewed_plan_hash', 'calculation'})
        if not isinstance(value, dict) or set(value) != required or value['schema_version'] != SCHEMA_VERSION:
            raise ValueError('Unsupported stored schema')
        if note:
            _hash(value['pilot_id'], 'Pilot ID')
            _day(value['observed_on'])
            _text(value['text'], 'Observation', 2000)
            if value['verification'] != 'owner_reported':
                raise ValueError('Unsupported note verification')
        else:
            if (type(value['calculator_version']) is not int or value['calculator_version'] < 1
                    or value['status'] != 'saved_draft'):
                raise ValueError('Unsupported stored calculation')
            _request({'expected_dataset_id': value['source']['dataset_id'],
                      'expected_revision': value['source']['revision'],
                      **{key: value[key] for key in ('reviewed_plan_hash', 'title', 'hypothesis', 'measure', 'inputs')}})
            for key in ('segment', 'proposed_audience_cap', 'maximum_offer_cost_cents',
                        'feasible_pilot', 'within_budget', 'budget_cents'):
                value['calculation'][key]
        return value
    except (ValueError, TypeError, KeyError):
        raise PilotStorageError('Saved pilot data is unavailable or uses an unsupported format.') from None

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class PilotStore:
    """Store aggregate drafts alongside datasets, without changing selection."""

    def __init__(self, dataset_store: DatasetStore):
        self.datasets = dataset_store
        with self.datasets.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS pilot_records ('
                       'id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, '
                       'payload TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS pilot_notes ('
                       'id TEXT PRIMARY KEY, pilot_id TEXT NOT NULL, '
                       'payload TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS pilot_notes_record '
                       'ON pilot_notes(pilot_id, created_at, id)')

    @staticmethod
    def _read(db: Any, pilot_id: str) -> dict[str, Any] | None:
        row = db.execute('SELECT payload,created_at FROM pilot_records WHERE id=?',
                         (pilot_id,)).fetchone()
        if row is None:
            return None
        notes = [{**_stored(raw, note=True), 'id': key, 'created_at': created}
                 for key, raw, created in db.execute(
                     'SELECT id,payload,created_at FROM pilot_notes '
                     'WHERE pilot_id=? ORDER BY created_at,id', (pilot_id,))]
        return {**_stored(row[0]), 'id': pilot_id, 'created_at': row[1], 'notes': notes}

    def get(self, pilot_id: str) -> dict[str, Any]:
        """Return the saved evidence; never recalculate against active data."""
        pilot_id = _hash(pilot_id, 'Pilot ID')
        with self.datasets.connect() as db:
            db.execute('BEGIN')
            result = self._read(db, pilot_id)
        if result is None:
            raise PilotNotFound('Saved pilot draft not found.')
        return result

    def save(self, request: Mapping[str, Any],
             load_snapshot: Callable[[str, str], dict[str, Any]]) -> dict[str, Any]:
        """Deduplicate first; bind one snapshot only for a genuinely new save.

        load_snapshot receives the explicit start/end ISO dates. It should load
        the active dataset once and return its dashboard with source.dataset_id.
        """
        normalized = _request(request)
        pilot_id = _digest(normalized)
        with self.datasets.connect() as db:
            existing = self._read(db, pilot_id)
        if existing is not None:
            return existing
        inputs = normalized['inputs']
        snapshot = load_snapshot(inputs['start'], inputs['end'])
        source = _source(snapshot)
        if (source['dataset_id'] != normalized['expected_dataset_id']
                or source['revision'] != normalized['expected_revision']
                or snapshot['period'] != {key: inputs[key] for key in ('start', 'end')}):
            raise PilotConflict('The dataset or period changed. Calculate and review the pilot again.')
        reviewed = review_plan(snapshot, **{key: inputs[key] for key in
            ('segment', 'max_customers', 'offer_cents', 'window_days', 'budget_cents')})
        if reviewed['reviewed_plan_hash'] != normalized['reviewed_plan_hash']:
            raise PilotConflict('The reviewed calculation changed. Calculate and review the pilot again.')
        payload = {
            'schema_version': SCHEMA_VERSION, 'calculator_version': CALCULATOR_VERSION,
            'status': 'saved_draft',
            'title': normalized['title'], 'hypothesis': normalized['hypothesis'],
            'measure': normalized['measure'],
            'business': {key: deepcopy(snapshot['business'][key]) for key in
                         ('id', 'name', 'currency', 'timezone')},
            'source': source, 'period': deepcopy(snapshot['period']), 'inputs': inputs,
            'reviewed_plan_hash': reviewed['reviewed_plan_hash'],
            'calculation': {key: value for key, value in reviewed.items() if key not in REVIEW_EXTRAS},
        }
        with self.datasets.connect() as db:
            # Serialize the count-and-insert and recheck duplicate saves that
            # completed while the shared calculation ran outside this lock.
            db.execute('BEGIN IMMEDIATE')
            existing = self._read(db, pilot_id)
            if existing is not None:
                return existing
            if db.execute('SELECT COUNT(*) FROM pilot_records').fetchone()[0] >= MAX_PILOTS:
                raise PilotLimitExceeded(f'At most {MAX_PILOTS} saved drafts are supported.')
            db.execute('INSERT INTO pilot_records(id,dataset_id,payload,created_at) VALUES(?,?,?,?)',
                       (pilot_id, source['dataset_id'], _json(payload), _now()))
            return self._read(db, pilot_id)

    def list(self, *, limit: int = 20, offset: int = 0,
             dataset_id: str | None = None) -> dict[str, Any]:
        limit = _integer(limit, 'List limit', 1, 50)
        offset = _integer(offset, 'List offset', 0, 1000)
        where, values = (' WHERE p.dataset_id=?', (_dataset_id(dataset_id),)) if dataset_id is not None else ('', ())
        with self.datasets.connect() as db:
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM pilot_records p' + where, values).fetchone()[0]
            rows = db.execute('SELECT p.id,p.payload,p.created_at,'
                              '(SELECT COUNT(*) FROM pilot_notes n WHERE n.pilot_id=p.id) '
                              'FROM pilot_records p' + where +
                              ' ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?',
                              (*values, limit, offset)).fetchall()
        summaries = []
        for key, raw, created, note_count in rows:
            stored = _stored(raw)
            summaries.append({**{name: stored[name] for name in
                              ('status', 'title', 'business', 'source', 'period', 'inputs')},
                              'id': key, 'created_at': created, 'note_count': note_count,
                              'calculation': {name: stored['calculation'][name] for name in
                                  ('segment', 'proposed_audience_cap', 'maximum_offer_cost_cents',
                                   'feasible_pilot', 'within_budget', 'budget_cents')}})
        return {'pilots': summaries, 'pagination': {
            'offset': offset, 'limit': limit, 'total': total,
            'next_offset': offset + len(rows) if offset + len(rows) < total else None}}

    def append_note(self, pilot_id: str, *, observed_on: str, text: str) -> dict[str, Any]:
        """Append a deduplicated owner statement, without deriving any result."""
        pilot_id = _hash(pilot_id, 'Pilot ID')
        payload = {'schema_version': SCHEMA_VERSION, 'pilot_id': pilot_id,
                   'observed_on': _day(observed_on), 'text': _text(text, 'Observation', 2000),
                   'verification': 'owner_reported'}
        note_id = _digest(payload)
        with self.datasets.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM pilot_records WHERE id=?', (pilot_id,)).fetchone():
                raise PilotNotFound('Saved pilot draft not found.')
            existing = db.execute('SELECT payload,created_at FROM pilot_notes WHERE id=?',
                                  (note_id,)).fetchone()
            if existing is not None:
                return {**_stored(existing[0], note=True), 'id': note_id, 'created_at': existing[1]}
            count = db.execute('SELECT COUNT(*) FROM pilot_notes WHERE pilot_id=?',
                               (pilot_id,)).fetchone()[0]
            if count >= MAX_NOTES:
                raise PilotLimitExceeded(f'At most {MAX_NOTES} observations per draft are supported.')
            created = _now()
            db.execute('INSERT INTO pilot_notes(id,pilot_id,payload,created_at) VALUES(?,?,?,?)',
                       (note_id, pilot_id, _json(payload), created))
            return {**payload, 'id': note_id, 'created_at': created}