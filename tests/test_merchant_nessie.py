"""Documented Nessie shapes, mocked HTTP reads, and local immutable-save tests."""
import asyncio
import copy
import hashlib
import json
import runpy
from datetime import date
from pathlib import Path

import httpx
import pytest

from spending_api import merchant_nessie as nessie
from spending_api.merchant import build_dashboard
from spending_api.merchant_import import DatasetStore, encode_dataset

START, END = date(2026, 11, 1), date(2026, 11, 10)
MERCHANT = "merchant-42"


def account(identifier, customer):
    return {"id": identifier, "customer_id": customer, "type": "Checking",
            "nickname": "PRIVATE ACCOUNT NAME", "balance_cents": 987654321,
            "pending_debits_cents": 0, "available_cents": 987654321,
            "account_number": "PRIVATE ACCOUNT NUMBER", "rewards": 0,
            "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}


def tx(identifier="purchase-1", **changes):
    return {"id": identifier, "kind": "purchase", "status": "completed", "medium": "balance",
            "payer_id": "account-1", "merchant_id": MERCHANT, "amount_cents": 1000,
            "created_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-01T10:00:00Z",
            "settle_at": "2026-11-01T10:00:00Z", "description": "PRIVATE DESCRIPTION", **changes}


def raw():
    return {
        "merchant": {"id": MERCHANT, "name": "PRIVATE MERCHANT NAME",
                     "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"},
        "accounts": [account("account-1", "customer-1"), account("account-2", "customer-1"),
                     account("account-3", "customer-2"), account("account-4", "customer-other")],
        "transactions": [
            tx(),
            tx("purchase-2", payer_id="account-2", amount_cents=2000, created_at="2026-11-02T10:00:00Z"),
            tx("purchase-3", payer_id="account-3", amount_cents=1500, created_at="2026-11-03T10:00:00Z"),
            tx("unrelated-purchase", payer_id="account-4", merchant_id="another-merchant", amount_cents=40000),
            tx("pending-purchase", status="pending"),
            tx("declined-purchase", status="declined"),
            tx("refund-1", kind="refund", merchant_id=None, parent_transaction_id="purchase-1",
               amount_cents=400, created_at="2026-11-04T10:00:00Z"),
            tx("forced-refund", kind="refund", merchant_id=None, amount_cents=50),
            tx("deposit", kind="deposit", merchant_id=None),
            tx("chargeback", kind="chargeback", parent_transaction_id="purchase-1", amount_cents=100),
        ],
    }


def normalize(value=None, **options):
    return nessie.normalize_merchant(value or raw(), merchant_id=MERCHANT,
        coverage_start=options.get("start", START), coverage_end=options.get("end", END))


def test_selected_purchases_and_refunds_exact_and_private():
    value = raw()
    before = copy.deepcopy(value)
    data, preview = normalize(value)
    assert value == before
    assert len(data.orders) == 3 and len(data.refunds) == 1
    assert len({row.customer_id for row in data.orders}) == 2
    assert preview["gross_revenue_cents"] == 4500
    assert preview["refund_cents"] == 400
    assert preview["net_revenue_cents"] == 4100
    assert preview["synthetic"] is data.synthetic is True
    assert MERCHANT in data.label
    serialized = encode_dataset(data)
    for private in ("PRIVATE", "account-1", "customer-1", "unrelated-purchase",
                    "pending-purchase", "another-merchant", "987654321"):
        assert private not in serialized
    assert all(len(row.customer_id) == 80 for row in data.orders)
    assert preview["preview_hash"] == hashlib.sha256(serialized.encode()).hexdigest()
    dashboard = build_dashboard(START, END, data)
    assert dashboard["metrics"]["net_revenue_cents"] == 4100


def test_output_deterministic_across_page_order_and_identical_overlaps():
    value = raw()
    expected = normalize(value)
    value["accounts"] = list(reversed(value["accounts"])) + [copy.deepcopy(value["accounts"][0])]
    value["transactions"] = list(reversed(value["transactions"])) + [copy.deepcopy(value["transactions"][0])]
    assert normalize(value) == expected


@pytest.mark.parametrize("collection", ["accounts", "transactions"])
@pytest.mark.parametrize("replacement", [True, 1.0, "1"])
def test_conflicting_duplicate_type_sensitive_even_before_scope(collection, replacement):
    value = raw()
    value[collection][0]["marker"] = 1
    other = copy.deepcopy(value[collection][0])
    other["marker"] = replacement
    value[collection].append(other)
    with pytest.raises(nessie.NessieImportError, match="Conflicting duplicate"):
        normalize(value)


@pytest.mark.parametrize("field,value", [
    ("amount_cents", True), ("amount_cents", 1.5), ("amount_cents", "1000"),
    ("amount_cents", 0), ("amount_cents", -100), ("amount_cents", 10_000_000_000),
    ("created_at", "2026-11-01T00:00:00"), ("created_at", "2026-11-01"),
    ("payer_id", "missing-account"), ("payee_id", "account-2"),
])
def test_selected_invalid_fields_fail_closed(field, value):
    records = raw()
    records["transactions"][0][field] = value
    with pytest.raises(nessie.NessieImportError):
        normalize(records)


def test_account_to_customer_mapping_required():
    value = raw()
    del value["accounts"][0]["customer_id"]
    with pytest.raises(nessie.NessieImportError, match="identity"):
        normalize(value)


def test_utc_filter_is_inclusive_and_not_provider_local_date():
    value = raw()
    value["transactions"] = [
        tx("first", created_at="2026-10-31T23:30:00-02:00"),
        tx("last", created_at="2026-11-11T01:30:00+02:00"),
        tx("excluded", created_at="2026-11-10T23:30:00-02:00"),
    ]
    data, preview = normalize(value)
    assert [row.occurred_on for row in data.orders] == [START, END]
    assert preview["orders"] == 2


@pytest.mark.parametrize("changes", [
    {"payer_id": "account-2"},
    {"payee_id": "account-2"},
    {"parent_transaction_id": "unrelated-purchase", "merchant_id": MERCHANT},
    {"parent_transaction_id": None, "merchant_id": MERCHANT},
    {"parent_transaction_id": "missing", "merchant_id": MERCHANT},
    {"merchant_id": "another-merchant"},
    {"amount_cents": 1001},
    {"created_at": "2026-11-01T09:59:59Z"},
])
def test_invalid_attributable_refund_rejected(changes):
    value = raw()
    value["transactions"][6].update(changes)
    with pytest.raises(nessie.NessieImportError):
        normalize(value)


def test_multiple_refunds_cannot_exceed_purchase():
    value = raw()
    value["transactions"].append(tx("refund-2", kind="refund", merchant_id=None,
        parent_transaction_id="purchase-1", amount_cents=700, created_at="2026-11-05T00:00:00Z"))
    with pytest.raises(nessie.NessieImportError, match="exceed"):
        normalize(value)


def test_refund_parent_status_and_coverage_are_required():
    value = raw()
    value["transactions"][0]["status"] = "pending"
    with pytest.raises(nessie.NessieImportError, match="completed parent"):
        normalize(value)
    value = raw()
    value["transactions"][0]["created_at"] = "2026-10-31T10:00:00Z"
    with pytest.raises(nessie.NessieImportError, match="extend the coverage start"):
        normalize(value)
    data, preview = normalize(value, start=date(2026, 10, 31))
    assert len(data.refunds) == 1 and preview["refund_cents"] == 400


def test_unknown_refunds_do_not_become_selected_merchant_refunds():
    value = raw()
    value["transactions"].append(tx("unknown-refund", kind="refund", merchant_id=None,
        parent_transaction_id="unavailable", amount_cents=100))
    data, preview = normalize(value)
    assert len(data.refunds) == 1
    assert any("unavailable parent" in warning for warning in preview["warnings"])


def test_merchant_identity_and_empty_selection_rejected():
    value = raw()
    value["merchant"]["id"] = "another"
    with pytest.raises(nessie.NessieImportError, match="different merchant"):
        normalize(value)
    value = raw()
    value["transactions"] = []
    with pytest.raises(nessie.NessieImportError, match="No completed purchases"):
        normalize(value)


@pytest.mark.parametrize("start,end", [(END, START), (date(1999, 1, 1), END),
    (START, date(2029, 1, 1)), ("2026-11-01", END)])
def test_coverage_validation(start, end):
    with pytest.raises(nessie.NessieImportError):
        normalize(start=start, end=end)


def fetch(handler, **options):
    return asyncio.run(nessie.import_from_nessie(
        merchant_id=MERCHANT, coverage_start=START, coverage_end=END,
        api_key="fake-private-key", transport=httpx.MockTransport(handler), **options))


def test_http_fixed_host_read_only_auth_and_pagination():
    value = raw()
    seen = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "v2-prod-api.nessieisreal.com"
        assert request.url.scheme == "https"
        assert request.headers["authorization"] == "Bearer fake-private-key"
        assert "fake-private-key" not in str(request.url)
        seen.append((request.url.path, request.url.params.get("cursor")))
        if request.url.path == f"/merchants/{MERCHANT}":
            return httpx.Response(200, json=value["merchant"])
        key = request.url.path.removeprefix("/")
        assert key in {"accounts", "transactions"}
        assert request.url.params["limit"] == "100"
        if request.url.params.get("cursor") is None:
            return httpx.Response(200, json={"items": value[key][:2], "next_cursor": "second"})
        return httpx.Response(200, json={"items": value[key][2:] + value[key][:1]})

    actual = fetch(handler)
    assert actual == normalize(value)
    assert len(seen) == 5


@pytest.mark.parametrize("payload", [
    {"items": [], "next_cursor": 4}, {"items": "wrong"},
    {"unexpected": []}, {"items": [], "next_cursor": "loop"},
])
def test_invalid_or_repeated_pagination_returns_no_partial_data(payload):
    value = raw()

    def handler(request):
        if request.url.path.startswith("/merchants/"):
            return httpx.Response(200, json=value["merchant"])
        return httpx.Response(200, json=payload)

    with pytest.raises(nessie.NessieImportError):
        fetch(handler)


def test_page_limit_is_not_partial_success(monkeypatch):
    monkeypatch.setattr(nessie, "MAX_PAGES", 2)
    counter = {"accounts": 0, "transactions": 0}

    def handler(request):
        if request.url.path.startswith("/merchants/"):
            return httpx.Response(200, json=raw()["merchant"])
        name = request.url.path.removeprefix("/")
        counter[name] += 1
        return httpx.Response(200, json={"items": [], "next_cursor": f"page-{counter[name]}"})

    with pytest.raises(nessie.NessieImportError, match="pagination exceeded"):
        fetch(handler)


@pytest.mark.parametrize("code", [301, 302, 401, 403, 429, 500])
def test_http_failure_does_not_follow_redirect_or_expose_body(code):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(code, headers={"location": "https://untrusted.example/private"},
                              text="PRIVATE PROVIDER BODY fake-private-key")

    with pytest.raises(nessie.NessieImportError) as exc:
        fetch(handler)
    assert "PRIVATE" not in str(exc.value) and "fake-private-key" not in str(exc.value)
    assert all("untrusted.example" not in url for url in calls)


def test_duplicate_json_fields_and_response_size_fail(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b'{"id":"merchant-42","id":"other","items":[]}')
    with pytest.raises(nessie.NessieImportError, match="invalid JSON"):
        fetch(handler)
    monkeypatch.setattr(nessie, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(nessie.NessieImportError, match="bounded import size"):
        fetch(lambda request: httpx.Response(200, content=b" " * 100))


def test_missing_credential_never_contacts_provider():
    called = []

    async def run():
        await nessie.import_from_nessie(merchant_id=MERCHANT, coverage_start=START, coverage_end=END,
            api_key="", transport=httpx.MockTransport(lambda request: called.append(request)))

    with pytest.raises(nessie.NessieImportError, match="NESSIE_API_KEY"):
        asyncio.run(run())
    assert called == []


def cli():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "import_merchant_nessie.py"))["main"]


def cli_args(path, *extra):
    return ["--merchant-id", MERCHANT, "--coverage-start", START.isoformat(),
            "--coverage-end", END.isoformat(), "--data-dir", str(path), *extra]


def test_cli_preview_does_not_create_storage_and_never_loads_env(tmp_path, monkeypatch, capsys):
    calls = []

    async def fake_fetch(**kwargs):
        calls.append(kwargs)
        return normalize()

    monkeypatch.setenv("NESSIE_API_KEY", "process-only-fake-key")
    # A .env would enable a different key if this command loaded it.
    (tmp_path / ".env").write_text("NESSIE_API_KEY=DO_NOT_LOAD", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    store_path = tmp_path / "new-storage"
    assert cli()(cli_args(store_path), fetch=fake_fetch) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["saved"] is result["selected"] is False
    assert not store_path.exists()
    assert calls[0]["api_key"] == "process-only-fake-key"


def test_cli_save_is_immutable_and_selection_requires_explicit_flag(tmp_path, monkeypatch, capsys):
    count = []

    async def fake_fetch(**kwargs):
        count.append(1)
        return normalize()

    monkeypatch.setenv("NESSIE_API_KEY", "process-only-fake-key")
    run = cli()
    assert run(cli_args(tmp_path, "--save"), fetch=fake_fetch) == 0
    first = json.loads(capsys.readouterr().out)
    store = DatasetStore(tmp_path)
    assert store.active()[0] == "demo"
    assert len(store.list()) == 2
    assert run(cli_args(tmp_path, "--save", "--select"), fetch=fake_fetch) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["dataset_id"] == second["dataset_id"]
    assert store.active()[0] == first["dataset_id"]
    assert len(store.list()) == 2
    assert len(count) == 2  # Exactly one capture per invocation; save uses that capture.


def test_cli_select_requires_save(tmp_path):
    with pytest.raises(SystemExit):
        cli()(cli_args(tmp_path, "--select"))


def test_cli_wrong_preview_hash_cannot_save_or_change_selection(tmp_path, capsys):
    async def fake_fetch(**kwargs):
        data, preview = normalize()
        return data, {**preview, "preview_hash": "0" * 64}

    assert cli()(cli_args(tmp_path, "--save", "--select"), fetch=fake_fetch) == 1
    output = capsys.readouterr()
    assert "successful save" in output.err
    assert DatasetStore(tmp_path).active()[0] == "demo"
    assert len(DatasetStore(tmp_path).list()) == 1

@pytest.mark.parametrize("index,field,value", [
    (0, "status", "unexpected"), (0, "kind", "unknown-purchase"),
    (6, "status", "unexpected"),
])
def test_unknown_selected_status_or_kind_is_not_silently_dropped(index, field, value):
    records = raw()
    records["transactions"][index][field] = value
    with pytest.raises(nessie.NessieImportError, match="unknown"):
        normalize(records)

def test_saved_nessie_limitations_survive_selection_and_dashboard(tmp_path):
    data, preview = normalize()
    store = DatasetStore(tmp_path)
    key = store.add(data, preview["preview_hash"], select=False)
    assert store.active()[0] == "demo"
    store.select(key)
    active_key, restored = store.active()
    assert active_key == key
    assert restored.source_notes == tuple(nessie.WARNINGS)
    dashboard = build_dashboard(START, END, restored)
    limitations = dashboard["source"]["limitations"]
    assert all(note in limitations for note in nessie.WARNINGS)
    assert any("created_at" in note for note in limitations)
    assert any("chargebacks" in note.lower() for note in limitations)
    assert dashboard["source"]["synthetic"] is True
