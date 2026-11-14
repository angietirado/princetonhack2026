import pytest
from spending_api.merchant_import import DatasetStore, parse_csv

CSV = 'kind,id,customer_id,order_id,date,amount_cents,status\norder,o1,c1,,2026-11-01,1000,completed\norder,o2,c1,,2026-11-02,1500,completed\nrefund,r1,,o1,2026-11-03,200,\n'


def test_import_preview_and_durable_selection(tmp_path):
    data, preview = parse_csv(CSV, business_name='Corner Cafe')
    assert preview['orders'] == 2 and preview['refunds'] == 1 and preview['customers'] == 1
    store = DatasetStore(tmp_path)
    assert store.active() == ('demo', None)
    key = store.add(data, preview['preview_hash'])
    assert DatasetStore(tmp_path).active()[1] == data
    store.add(data, preview['preview_hash'])
    assert len(store.list()) == 2
    store.select('demo')
    assert store.active() == ('demo', None)
    store.select(key)
    assert store.active()[1].business_name == 'Corner Cafe'


def test_preview_hash_protects_reviewed_data(tmp_path):
    data, preview = parse_csv(CSV, business_name='Cafe')
    changed, _ = parse_csv(CSV.replace('1000', '2000'), business_name='Cafe')
    store = DatasetStore(tmp_path)
    with pytest.raises(ValueError, match='changed after preview'):
        store.add(changed, preview['preview_hash'])
    assert store.active() == ('demo', None)


@pytest.mark.parametrize('bad', [
    CSV.replace('1000', '10.00'), CSV.replace('1000', '-1000'),
    CSV.replace('o2', 'o1'), CSV.replace('200,', '2000,'),
    CSV.replace('c1', ''), CSV.replace('c1', 'name@example.com'),
    CSV.replace('2026-11-01', '2026-02-30'), CSV.replace('2026-11-01', '20261101'),
    CSV.replace('refund,r1,,o1', 'refund,r1,,unknown'),
    CSV.replace('kind,id', 'type,id'),
])
def test_invalid_import_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_csv(bad, business_name='Cafe')


def test_provenance_changes_preview_hash():
    _, a = parse_csv(CSV, business_name='Cafe', synthetic=True)
    _, b = parse_csv(CSV, business_name='Cafe', synthetic=False)
    _, c = parse_csv(CSV, business_name='Different cafe', synthetic=True)
    assert len({a['preview_hash'], b['preview_hash'], c['preview_hash']}) == 3


def test_saving_without_selection_preserves_active_dataset(tmp_path):
    data, preview = parse_csv(CSV, business_name='Saved for later', synthetic=True)
    store = DatasetStore(tmp_path)
    key = store.add(data, preview['preview_hash'], select=False)
    assert store.active() == ('demo', None)
    assert key in {row['id'] for row in store.list()}
    store.select(key)
    assert store.active()[1] == data


def test_unterminated_quoted_header_is_a_validation_error():
    with pytest.raises(ValueError, match='header quoting'):
        parse_csv('"kind,id,customer_id,order_id,date,amount_cents,status\norder,o1,c1,,2026-11-01,1000,completed\n', business_name='Cafe')
