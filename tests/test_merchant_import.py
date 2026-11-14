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


def test_preview_explains_money_and_excluded_order_statuses():
    extra = 'order,pending_1,c2,,2026-11-02,90000,pending\norder,cancelled_1,c3,,2026-11-03,40000,cancelled\n'
    data, preview = parse_csv(CSV + extra, business_name='Cafe')
    assert preview['orders'] == 4
    assert preview['completed_orders'] == 2
    assert preview['excluded_orders'] == 2
    assert preview['pending_orders'] == preview['cancelled_orders'] == 1
    assert preview['customers'] == 1
    assert preview['gross_revenue_cents'] == 2500
    assert preview['refund_cents'] == 200
    assert preview['net_revenue_cents'] == 2300
    from spending_api.merchant import build_dashboard
    snapshot = build_dashboard(data.coverage_start, data.coverage_end, data)
    assert preview['net_revenue_cents'] == snapshot['metrics']['net_revenue_cents']


def test_store_releases_handles_without_waiting_for_garbage_collection(tmp_path):
    import gc
    from pathlib import Path
    descriptors = Path('/proc/self/fd')
    if not descriptors.exists():
        pytest.skip('Linux file-descriptor check runs in the Docker workflow')
    store = DatasetStore(tmp_path)
    gc.collect()
    originally_enabled = gc.isenabled()
    gc.disable()
    try:
        before = len(list(descriptors.iterdir()))
        for _ in range(40):
            store.list()
            store.active()
        with pytest.raises(RuntimeError):
            with store.connect() as db:
                db.execute("UPDATE selection SET dataset_id='invalid' WHERE singleton=1")
                raise RuntimeError('abort the transaction')
        assert store.active() == ('demo', None)
        assert len(list(descriptors.iterdir())) <= before
    finally:
        if originally_enabled:
            gc.enable()
