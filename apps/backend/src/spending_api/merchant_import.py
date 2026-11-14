"""Strict CSV previews and immutable, atomic local dataset storage."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path

from spending_api.merchant import Dataset, Order, Refund, _validate

COLUMNS = ('kind', 'id', 'customer_id', 'order_id', 'date', 'amount_cents', 'status')
MAX_ROWS = 5000
MAX_BYTES = 750_000


def parse_csv(text: str, *, business_name: str, synthetic: bool = False,
              coverage_start: str | None = None, coverage_end: str | None = None) -> tuple[Dataset, dict]:
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('CSV must be UTF-8 text, at most 750 KB.')
    if not isinstance(business_name, str) or not 1 <= len(business_name.strip()) <= 100:
        raise ValueError('Business name must be 1–100 characters.')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), strict=True)
    try:
        header = reader.fieldnames
    except csv.Error:
        raise ValueError('CSV header quoting is malformed.') from None
    if header != list(COLUMNS):
        raise ValueError('Use the exact template columns: ' + ','.join(COLUMNS))
    orders, refunds, dates = [], [], []
    try:
        for number, row in enumerate(reader, 2):
            if number > MAX_ROWS + 1:
                raise ValueError('CSV is limited to 5,000 records.')
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Row {number}: wrong column count.')
            row = {key: value.strip() for key, value in row.items()}
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', row['id']):
                raise ValueError(f'Row {number}: use an opaque record ID with letters, digits, underscores or hyphens.')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['date']):
                raise ValueError(f'Row {number}: date must be YYYY-MM-DD.')
            try:
                occurred = date.fromisoformat(row['date'])
            except ValueError:
                raise ValueError(f'Row {number}: invalid calendar date.') from None
            if not re.fullmatch(r'\d{1,10}', row['amount_cents']):
                raise ValueError(f'Row {number}: amount_cents must be a nonnegative whole number, not dollars.')
            amount = int(row['amount_cents'])
            if row['kind'] == 'order':
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', row['customer_id']):
                    raise ValueError(f'Row {number}: provide an opaque customer ID; anonymous orders are not supported yet.')
                if row['order_id'] or row['status'] not in {'completed', 'pending', 'cancelled'}:
                    raise ValueError(f'Row {number}: orders need a valid status and an empty order_id.')
                orders.append(Order(row['id'], row['customer_id'], occurred, amount, row['status']))
            elif row['kind'] == 'refund':
                if row['customer_id'] or row['status'] or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', row['order_id']):
                    raise ValueError(f'Row {number}: refunds need order_id, with customer_id and status empty.')
                refunds.append(Refund(row['id'], row['order_id'], occurred, amount))
            else:
                raise ValueError(f'Row {number}: kind must be order or refund.')
            dates.append(occurred)
    except csv.Error:
        raise ValueError('CSV quoting is malformed.') from None
    if not orders:
        raise ValueError('Include at least one order.')
    start = date.fromisoformat(coverage_start) if coverage_start else min(dates)
    end = date.fromisoformat(coverage_end) if coverage_end else max(dates)
    if (end - start).days > 730:
        raise ValueError('Coverage is limited to 731 inclusive days.')
    data = Dataset(tuple(orders), tuple(refunds), coverage_start=start, coverage_end=end,
                   as_of=end, label='Imported owner sales CSV', synthetic=synthetic,
                   business_name=business_name.strip())
    _validate(data)
    digest = hashlib.sha256(encode_dataset(data).encode()).hexdigest()
    completed = [row for row in orders if row.status == 'completed']
    gross = sum(row.amount_cents for row in completed)
    refunded = sum(row.amount_cents for row in refunds)
    preview = {
        'preview_hash': digest, 'business_name': data.business_name,
        'orders': len(orders), 'refunds': len(refunds),
        'completed_orders': len(completed), 'excluded_orders': len(orders) - len(completed),
        'pending_orders': sum(row.status == 'pending' for row in orders),
        'cancelled_orders': sum(row.status == 'cancelled' for row in orders),
        'gross_revenue_cents': gross, 'refund_cents': refunded,
        'net_revenue_cents': gross - refunded,
        'customers': len({row.customer_id for row in orders if row.status == 'completed'}),
        'coverage_start': start.isoformat(), 'coverage_end': end.isoformat(),
        'synthetic': synthetic,
        'warnings': [
            'Coverage describes supplied records; it does not prove complete sales history.',
            'Customer IDs must be stable pseudonyms. Do not upload names, emails, phone numbers or payment details.',
            'This import creates a separate immutable dataset; original and demo records are retained.',
            'Dates are UTC calendar dates. Convert local timestamps before importing.',
        ],
    }
    return data, preview


def encode_dataset(data: Dataset) -> str:
    return json.dumps(asdict(data), default=lambda value: value.isoformat(), sort_keys=True,
                      separators=(',', ':'), ensure_ascii=False)


def decode_dataset(raw: str) -> Dataset:
    value = json.loads(raw)
    value['orders'] = tuple(Order(**{**row, 'occurred_on': date.fromisoformat(row['occurred_on'])}) for row in value['orders'])
    value['refunds'] = tuple(Refund(**{**row, 'occurred_on': date.fromisoformat(row['occurred_on'])}) for row in value['refunds'])
    for key in ('coverage_start', 'coverage_end', 'as_of'):
        value[key] = date.fromisoformat(value[key])
    if 'source_notes' in value:
        value['source_notes'] = tuple(value['source_notes'])
    data = Dataset(**value)
    _validate(data)
    return data


class DatasetStore:
    def __init__(self, directory: str | Path):
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / 'owner-datasets.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE TABLE IF NOT EXISTS selection (singleton INTEGER PRIMARY KEY CHECK(singleton=1), dataset_id TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO selection VALUES(1,'demo')")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, data: Dataset, expected_hash: str, *, select: bool = True) -> str:
        raw = encode_dataset(data)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        if digest != expected_hash:
            raise ValueError('CSV or import options changed after preview. Preview again before importing.')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO datasets(id,payload) VALUES(?,?)', (digest, raw))
            if select:
                db.execute('UPDATE selection SET dataset_id=? WHERE singleton=1', (digest,))
        return digest

    def active(self) -> tuple[str, Dataset | None]:
        with self.connect() as db:
            key = db.execute('SELECT dataset_id FROM selection WHERE singleton=1').fetchone()[0]
            if key == 'demo':
                return key, None
            row = db.execute('SELECT payload FROM datasets WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('Selected dataset is unavailable.')
        return key, decode_dataset(row[0])

    def select(self, key: str):
        with self.connect() as db:
            if key != 'demo' and not db.execute('SELECT 1 FROM datasets WHERE id=?', (key,)).fetchone():
                raise ValueError('Dataset not found.')
            db.execute('UPDATE selection SET dataset_id=? WHERE singleton=1', (key,))

    def list(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute('SELECT id,payload,created_at FROM datasets ORDER BY created_at DESC,id').fetchall()
        result = [{'id': 'demo', 'business_name': 'Harbor & Pine', 'synthetic': True, 'label': 'Built-in fictional cafe'}]
        for key, raw, created in rows:
            data = decode_dataset(raw)
            result.append({'id': key, 'business_name': data.business_name, 'synthetic': data.synthetic,
                           'label': data.label, 'created_at': created,
                           'coverage_start': data.coverage_start.isoformat(), 'coverage_end': data.coverage_end.isoformat()})
        return result
