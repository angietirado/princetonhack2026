"""Catalog tests use fictional local wallets only; no environment or HTTP access."""
import copy
import json

import pytest

from spending_api import lab_wallet_catalog as catalog
from spending_api.lab_financial import financial_summary
from spending_api.lab_wallet import freeze_wallet, make_fixture_wallets


@pytest.fixture
def record():
    return catalog.make_wallet_profile(
        profile_id="operator-week", profile_name="Operator week",
        description="Explicit synthetic test fixture.", snapshot=make_fixture_wallets()[0],
    )


def envelope(record):
    return {"schema_version": 1, "profiles": [copy.deepcopy(record)]}


def save_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_catalog_recomputes_compatible_context_and_preserves_source(record, tmp_path):
    original = copy.deepcopy(record)
    path = tmp_path / "wallets.json"
    context = catalog.write_wallet_profile(path, record)
    loaded = catalog.load_wallet_catalog(path)["operator-week"]
    assert context == loaded
    assert loaded["snapshot"]["source"] == "fixture"
    assert loaded["projection"]["source"] == "fixture"
    assert loaded["projection"]["status"] == "ready"
    assert loaded["projection"]["spendable_now_cents"] == 22000
    assert loaded["projection_inputs"] == {"days": 7, "reserve_cents": 0, "expected_income": [], "events": []}
    assert financial_summary({"financial_context": loaded}) == loaded["projection"]
    assert record == original
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert set(on_disk) == {"schema_version", "profiles"}
    assert "projection" not in on_disk["profiles"][0]
    loaded["snapshot"]["accounts"][0]["nickname"] = "Changed locally"
    assert catalog.load_wallet_catalog(path)["operator-week"]["snapshot"] == original["snapshot"]


def test_explicit_income_reserve_and_event_are_recomputed_without_inventing_income(record):
    record["projection_inputs"] = {
        "days": 7, "reserve_cents": 1000,
        "expected_income": [{"id": "explicit-pay", "label": "Explicit test income", "amount_cents": 50000, "available_at": "2026-11-17T00:00:00Z"}],
        "events": [{"kind": "car_unavailable"}],
    }
    result = catalog.validate_wallet_catalog(envelope(record))["operator-week"]["projection"]
    assert result["spendable_now_cents"] == 21000
    assert result["shopping_allowance_cents"] == 71000
    assert result["allowed_travel_modes"] == ["walk"]


def test_unknown_bill_date_remains_needs_review(record):
    snapshot = copy.deepcopy(record["snapshot"])
    snapshot["obligations"][0]["due_at"] = None
    record["snapshot"] = freeze_wallet(snapshot)
    result = catalog.validate_wallet_catalog(envelope(record))["operator-week"]["projection"]
    assert result["status"] == "needs_review"
    assert result["spendable_now_cents"] is None
    assert result["shopping_allowance_cents"] is None


@pytest.mark.parametrize("version", [0, 2, True, "1", None])
def test_unsupported_schema_versions_fail_closed(record, version):
    payload = envelope(record)
    payload["schema_version"] = version
    with pytest.raises(catalog.WalletCatalogError, match="invalid_envelope"):
        catalog.validate_wallet_catalog(payload)


@pytest.mark.parametrize("profiles", [[], {}, None])
def test_profiles_must_be_nonempty_bounded_list(record, profiles):
    with pytest.raises(catalog.WalletCatalogError):
        catalog.validate_wallet_catalog({"schema_version": 1, "profiles": profiles})


def test_profile_limit_duplicate_ids_and_unknown_fields(record):
    payload = {"schema_version": 1, "profiles": [dict(record, profile_id=f"profile-{i}") for i in range(21)]}
    with pytest.raises(catalog.WalletCatalogError, match="invalid_envelope"):
        catalog.validate_wallet_catalog(payload)
    with pytest.raises(catalog.WalletCatalogError, match="duplicate_profile_id"):
        catalog.validate_wallet_catalog({"schema_version": 1, "profiles": [record, copy.deepcopy(record)]})
    payload = envelope(record)
    payload["projection"] = {}
    with pytest.raises(catalog.WalletCatalogError, match="invalid_envelope"):
        catalog.validate_wallet_catalog(payload)
    payload = envelope(record)
    payload["profiles"][0]["projection"] = {"spendable_now_cents": 99999999}
    with pytest.raises(catalog.WalletCatalogError, match="invalid_profile"):
        catalog.validate_wallet_catalog(payload)


@pytest.mark.parametrize("identity", ["../wallet", "", "A", "a" * 65, "two words", "private\nid"])
def test_profile_identifiers_are_bounded_slugs_without_error_echo(record, identity):
    record["profile_id"] = identity
    with pytest.raises(catalog.WalletCatalogError) as raised:
        catalog.validate_wallet_catalog(envelope(record))
    assert str(raised.value) == "Wallet catalog rejected: invalid_profile_id"


@pytest.mark.parametrize(("field", "value"), [
    ("profile_name", ""), ("profile_name", " leading"), ("profile_name", "x" * 121),
    ("description", "x" * 601), ("profile_name", "secret\nidentifier"),
])
def test_labels_are_bounded_and_do_not_echo_input(record, field, value):
    record[field] = value
    with pytest.raises(catalog.WalletCatalogError, match="invalid_profile_label"):
        catalog.validate_wallet_catalog(envelope(record))


def test_tampered_snapshot_source_and_unexpected_sensitive_fields_fail(record):
    for mutate in (
        lambda snapshot: snapshot.update(available_cents=999999),
        lambda snapshot: snapshot.update(source="live-bank"),
        lambda snapshot: snapshot["accounts"][0].update(account_number="do-not-echo"),
        lambda snapshot: snapshot.update(api_key="do-not-echo"),
    ):
        payload = envelope(record)
        mutate(payload["profiles"][0]["snapshot"])
        with pytest.raises(catalog.WalletCatalogError) as raised:
            catalog.validate_wallet_catalog(payload)
        assert "do-not-echo" not in str(raised.value)


def test_projection_inputs_reject_invalid_or_hidden_decisions(record):
    for inputs in (
        {"reserve_cents": True}, {"days": 0}, {"days": 32},
        {"decisions": [{"amount_cents": 1}]},
        {"events": [{"kind": "delay_income", "income_id": "missing", "delay_days": 1}]},
    ):
        payload = envelope(record)
        payload["profiles"][0]["projection_inputs"] = inputs
        with pytest.raises(catalog.WalletCatalogError, match="invalid_financial_context"):
            catalog.validate_wallet_catalog(payload)


def test_total_financial_record_limit_is_enforced_before_projection(record, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_FINANCIAL_RECORDS", 3)
    with pytest.raises(catalog.WalletCatalogError, match="financial_record_limit"):
        catalog.validate_wallet_catalog(envelope(record))


@pytest.mark.parametrize("raw", [
    b'{"schema_version":1,"schema_version":1,"profiles":[]}',
    b'{"nested":{"duplicate":1,"duplicate":2}}',
    b'{"schema_version":NaN,"profiles":[]}', b'{"schema_version":Infinity}',
    b'{"schema_version":', b"\xff\xfe",
])
def test_malformed_json_is_never_replaced_with_fixtures(tmp_path, raw):
    path = tmp_path / "invalid.json"
    path.write_bytes(raw)
    with pytest.raises(catalog.WalletCatalogError):
        catalog.load_wallet_catalog(path)
    assert path.read_bytes() == raw


def test_size_and_file_type_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_CATALOG_BYTES", 100)
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * 101)
    with pytest.raises(catalog.WalletCatalogError, match="catalog_size_limit"):
        catalog.load_wallet_catalog(path)
    with pytest.raises(catalog.WalletCatalogError, match="catalog_not_regular"):
        catalog.load_wallet_catalog(tmp_path)
    with pytest.raises(catalog.WalletCatalogError, match="catalog_read_error"):
        catalog.load_wallet_catalog(tmp_path / "missing.json")


def test_create_append_replace_have_explicit_profile_semantics(record, tmp_path):
    path = tmp_path / "catalog.json"
    catalog.write_wallet_profile(path, record)
    before = path.read_bytes()
    for mode, code in (("create", "catalog_already_exists"), ("append", "profile_already_exists")):
        with pytest.raises(catalog.WalletCatalogError, match=code):
            catalog.write_wallet_profile(path, record, mode=mode)
        assert path.read_bytes() == before
    second = dict(record, profile_id="second", profile_name="Second week")
    with pytest.raises(catalog.WalletCatalogError, match="profile_missing"):
        catalog.write_wallet_profile(path, second, mode="replace")
    catalog.write_wallet_profile(path, second, mode="append")
    changed = dict(record, projection_inputs={"reserve_cents": 1000})
    catalog.write_wallet_profile(path, changed, mode="replace")
    loaded = catalog.load_wallet_catalog(path)
    assert set(loaded) == {"operator-week", "second"}
    assert loaded["operator-week"]["projection"]["spendable_now_cents"] == 21000
    assert loaded["second"]["projection"]["spendable_now_cents"] == 22000


@pytest.mark.parametrize("mode", ["append", "replace"])
def test_append_replace_do_not_implicitly_create_files(record, tmp_path, mode):
    with pytest.raises(catalog.WalletCatalogError, match="catalog_missing"):
        catalog.write_wallet_profile(tmp_path / "missing.json", record, mode=mode)
    assert list(tmp_path.iterdir()) == []


def test_invalid_existing_catalog_is_preserved_even_for_explicit_replace(record, tmp_path):
    path = tmp_path / "catalog.json"
    path.write_bytes(b"operator must review this invalid file")
    before = path.read_bytes()
    with pytest.raises(catalog.WalletCatalogError):
        catalog.write_wallet_profile(path, record, mode="replace")
    assert path.read_bytes() == before
    assert not (tmp_path / "catalog.json.lock").exists()


def test_atomic_replace_failure_leaves_old_bytes_and_cleans_own_temporary_files(record, tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    catalog.write_wallet_profile(path, record)
    before = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("do-not-echo sensitive path")

    monkeypatch.setattr(catalog.os, "replace", fail_replace)
    with pytest.raises(catalog.WalletCatalogError, match="catalog_write_error"):
        catalog.write_wallet_profile(path, dict(record, profile_name="Updated"), mode="replace")
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["catalog.json"]


def test_readers_observe_complete_old_and_new_envelopes(record, tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    catalog.write_wallet_profile(path, record)
    real_replace = catalog.os.replace

    def checked_replace(source, destination):
        assert catalog.load_wallet_catalog(path)["operator-week"]["profile_name"] == "Operator week"
        assert catalog.load_wallet_catalog(source)["operator-week"]["profile_name"] == "Updated"
        real_replace(source, destination)

    monkeypatch.setattr(catalog.os, "replace", checked_replace)
    catalog.write_wallet_profile(path, dict(record, profile_name="Updated"), mode="replace")
    assert catalog.load_wallet_catalog(path)["operator-week"]["profile_name"] == "Updated"


def test_occupied_lock_is_not_deleted_or_ignored(record, tmp_path):
    path = tmp_path / "catalog.json"
    lock = tmp_path / "catalog.json.lock"
    lock.write_bytes(b"another writer")
    with pytest.raises(catalog.WalletCatalogError, match="catalog_locked"):
        catalog.check_wallet_profile_target(path, "operator-week")
    with pytest.raises(catalog.WalletCatalogError, match="catalog_locked"):
        catalog.write_wallet_profile(path, record)
    assert lock.read_bytes() == b"another writer"
    assert not path.exists()


def test_oversize_write_does_not_replace_existing_catalog(record, tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    catalog.write_wallet_profile(path, record)
    before = path.read_bytes()
    monkeypatch.setattr(catalog, "MAX_CATALOG_BYTES", len(before) - 1)
    changed = dict(record, description="x" * 600)
    with pytest.raises(catalog.WalletCatalogError, match="catalog_size_limit"):
        catalog.write_wallet_profile(path, changed, mode="replace")
    assert path.read_bytes() == before


def test_shared_frozen_snapshot_can_have_explicitly_different_projection_profiles(record):
    second = dict(record, profile_id="reserve-branch", projection_inputs={"reserve_cents": 1000})
    contexts = catalog.validate_wallet_catalog({"schema_version": 1, "profiles": [record, second]})
    assert contexts["operator-week"]["snapshot"]["id"] == contexts["reserve-branch"]["snapshot"]["id"]
    assert contexts["operator-week"]["projection"]["spendable_now_cents"] == 22000
    assert contexts["reserve-branch"]["projection"]["spendable_now_cents"] == 21000


def test_create_never_overwrites_external_destination_created_after_preflight(record, tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    external = b"an external creator owns these bytes"
    real_link = catalog.os.link

    def competing_create(source, destination):
        # Deterministically introduce the race after validation/temp-file flush,
        # immediately before the atomic no-clobber publication operation.
        assert not path.exists()
        path.write_bytes(external)
        real_link(source, destination)

    monkeypatch.setattr(catalog.os, "link", competing_create)
    with pytest.raises(catalog.WalletCatalogError, match="catalog_already_exists"):
        catalog.write_wallet_profile(path, record, mode="create")
    assert path.read_bytes() == external
    assert sorted(item.name for item in tmp_path.iterdir()) == ["catalog.json"]


def test_create_fails_safely_when_filesystem_cannot_publish_hard_links(record, tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"

    def unsupported_link(_source, _destination):
        raise OSError("filesystem does not support hard links; do-not-echo path")

    monkeypatch.setattr(catalog.os, "link", unsupported_link)
    with pytest.raises(catalog.WalletCatalogError, match="catalog_write_error") as raised:
        catalog.write_wallet_profile(path, record, mode="create")
    assert "do-not-echo" not in str(raised.value)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
