"""Operator CLI tests inject an offline reader and explicit fake environments."""
import copy
import io
import json

import pytest

from scripts import capture_nessie_wallet as cli
from spending_api.lab_nessie import NessieReadError, normalize_nessie_snapshot
from spending_api.lab_wallet import freeze_wallet, make_fixture_wallets
from spending_api.lab_wallet_catalog import load_wallet_catalog, make_wallet_profile, write_wallet_profile

ENV = {"LAB_NESSIE_API_KEY": "fake-test-secret-never-a-real-key", "LAB_NESSIE_CUSTOMER_ID": "pinned-test-customer"}


@pytest.fixture
def captured():
    return normalize_nessie_snapshot({
        "accounts": [{
            "id": "checking-test", "customer_id": ENV["LAB_NESSIE_CUSTOMER_ID"], "type": "Checking",
            "nickname": "Test checking", "balance_cents": 10000, "pending_debits_cents": 0,
            "available_cents": 10000, "account_number": "fictional-number-must-not-persist",
        }],
        "bills": [{
            "id": "bill-test", "account_id": "checking-test", "status": "pending",
            "payee": "Test utility", "payment_amount": 12.50, "upcoming_payment_date": "2026-11-16",
            "created_at": "2026-11-01T00:00:00Z",
        }],
        "transactions": [],
    }, customer_id=ENV["LAB_NESSIE_CUSTOMER_ID"], captured_at="2026-11-14T00:00:00Z")


def run(tmp_path, captured, *, extra=(), environ=None, failure=None):
    calls = []

    class FakeReader:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def capture(self):
            if failure:
                raise failure
            return copy.deepcopy(captured)

    out, err = io.StringIO(), io.StringIO()
    code = cli.main([
        "--output", str(tmp_path / "catalog.json"), "--profile-id", "study-week",
        "--profile-name", "Study week", *extra,
    ], environ=ENV if environ is None else environ, reader_factory=FakeReader, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue(), calls


def test_cli_captures_once_from_explicit_environment_and_omits_credentials(captured, tmp_path):
    before = copy.deepcopy(captured)
    code, out, err, calls = run(tmp_path, captured, extra=["--reserve-cents", "500", "--days", "7"])
    assert code == 0
    assert calls == [{"api_key": ENV["LAB_NESSIE_API_KEY"], "customer_id": ENV["LAB_NESSIE_CUSTOMER_ID"]}]
    assert out == "Saved frozen Nessie wallet catalog. Projection status: ready.\n"
    assert err == ""
    raw = (tmp_path / "catalog.json").read_text(encoding="utf-8")
    for forbidden in (ENV["LAB_NESSIE_API_KEY"], "fictional-number-must-not-persist", "account_number", "api_key", "Authorization"):
        assert forbidden not in raw
    context = load_wallet_catalog(tmp_path / "catalog.json")["study-week"]
    assert context["snapshot"]["source"] == "nessie"
    assert context["snapshot"]["id"] == captured["id"]
    assert context["projection"]["spendable_now_cents"] == 8250
    assert context["projection_inputs"]["expected_income"] == []
    assert context["snapshot"]["obligations"][0]["amount_cents"] == 1250
    assert captured == before
    for identifier in (ENV["LAB_NESSIE_CUSTOMER_ID"], "checking-test", "bill-test", captured["id"], str(tmp_path)):
        assert identifier not in out + err


@pytest.mark.parametrize("environ", [
    {}, {"LAB_NESSIE_API_KEY": "fake"}, {"LAB_NESSIE_CUSTOMER_ID": "test"},
    {"LAB_NESSIE_API_KEY": " ", "LAB_NESSIE_CUSTOMER_ID": "test"},
    {"NESSIE_API_KEY": "legacy-do-not-use", "NESSIE_CUSTOMER_ID": "legacy-do-not-use"},
])
def test_missing_explicit_environment_never_calls_reader(captured, tmp_path, environ):
    code, out, err, calls = run(tmp_path, captured, environ=environ)
    assert code == 2
    assert calls == []
    assert out == ""
    assert "LAB_NESSIE_API_KEY" in err
    assert not (tmp_path / "catalog.json").exists()


def test_cli_does_not_read_dotenv_file(captured, tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("LAB_NESSIE_API_KEY=fake-dotenv-secret\nLAB_NESSIE_CUSTOMER_ID=not-authorized\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err, calls = run(tmp_path, captured, environ={})
    assert code == 2
    assert calls == []
    assert "fake-dotenv-secret" not in out + err
    assert dotenv.read_text(encoding="utf-8").startswith("LAB_NESSIE_API_KEY=fake-dotenv-secret")


@pytest.mark.parametrize("extra", [
    ["--api-key", "fake-cli-secret"], ["--customer-id", "private-cli-id"],
    ["--profile-id", "../bad"], ["--profile-name", ""], ["--days", "0"],
    ["--days", "32"], ["--reserve-cents", "-1"], ["--reserve-cents", "1.5"],
    ["--description", "x" * 601], ["--mode", "overwrite-everything"],
])
def test_invalid_arguments_do_not_echo_values_or_call_reader(captured, tmp_path, extra):
    code, out, err, calls = run(tmp_path, captured, extra=extra)
    assert code == 2
    assert calls == []
    assert out == ""
    assert "fake-cli-secret" not in err
    assert "private-cli-id" not in err
    assert "../bad" not in err
    assert not (tmp_path / "catalog.json").exists()


def test_cli_refuses_existing_file_before_capture_without_explicit_mode(captured, tmp_path):
    assert run(tmp_path, captured)[0] == 0
    before = (tmp_path / "catalog.json").read_bytes()
    code, out, err, calls = run(tmp_path, captured)
    assert code == 1 and calls == []
    assert "Create requires a new catalog" in err
    assert out == ""
    assert (tmp_path / "catalog.json").read_bytes() == before


def test_append_new_id_and_replace_existing_id_are_intentional(captured, tmp_path):
    assert run(tmp_path, captured)[0] == 0
    code, _, err, calls = run(tmp_path, captured, extra=["--mode", "append"])
    assert code == 1 and not calls
    assert "Append requires a new profile ID" in err
    assert run(tmp_path, captured, extra=["--mode", "append", "--profile-id", "study-second"])[0] == 0
    assert run(tmp_path, captured, extra=["--mode", "replace", "--reserve-cents", "1000"])[0] == 0
    contexts = load_wallet_catalog(tmp_path / "catalog.json")
    assert set(contexts) == {"study-week", "study-second"}
    assert contexts["study-week"]["projection"]["spendable_now_cents"] == 7750
    assert contexts["study-second"]["projection"]["spendable_now_cents"] == 8750


def test_occupied_lock_fails_before_capture_and_is_preserved(captured, tmp_path):
    lock = tmp_path / "catalog.json.lock"
    lock.write_text("active writer", encoding="utf-8")
    code, out, err, calls = run(tmp_path, captured)
    assert code == 1 and not calls
    assert "Catalog is locked" in err
    assert out == ""
    assert lock.read_text(encoding="utf-8") == "active writer"


@pytest.mark.parametrize("failure", [
    NessieReadError("network_error"),
    RuntimeError("https://fake.invalid?key=fake-test-secret-never-a-real-key&customer=pinned-test-customer"),
])
def test_reader_failure_preserves_existing_file_and_redacts_errors(captured, tmp_path, failure):
    assert run(tmp_path, captured)[0] == 0
    before = (tmp_path / "catalog.json").read_bytes()
    code, out, err, calls = run(tmp_path, captured, extra=["--mode", "replace"], failure=failure)
    assert code == 1
    assert len(calls) == 1 and out == ""
    assert (tmp_path / "catalog.json").read_bytes() == before
    for secret in ("fake.invalid", ENV["LAB_NESSIE_API_KEY"], ENV["LAB_NESSIE_CUSTOMER_ID"]):
        assert secret not in err
    assert sorted(item.name for item in tmp_path.iterdir()) == ["catalog.json"]


def test_cli_rejects_fixture_return_without_relabeling_it(tmp_path):
    fixture = make_fixture_wallets()[0]
    before = copy.deepcopy(fixture)
    code, out, err, _ = run(tmp_path, fixture)
    assert code == 1
    assert "did not return a Nessie snapshot" in err
    assert not (tmp_path / "catalog.json").exists()
    assert fixture == before and fixture["source"] == "fixture"
    assert out == ""


def test_cli_rejects_wrong_pinned_customer(captured, tmp_path):
    captured["customer_id"] = "different-test-customer"
    captured = freeze_wallet(captured)
    code, _, err, _ = run(tmp_path, captured)
    assert code == 1
    assert "pinned customer" in err
    assert "different-test-customer" not in err
    assert not (tmp_path / "catalog.json").exists()


def test_cli_saves_honest_needs_review_without_fallback(captured, tmp_path):
    captured["obligations"][0]["due_at"] = None
    captured = freeze_wallet(captured)
    code, out, err, _ = run(tmp_path, captured)
    assert code == 0 and err == ""
    assert "needs_review" in out
    context = load_wallet_catalog(tmp_path / "catalog.json")["study-week"]
    assert context["snapshot"]["source"] == "nessie"
    assert context["projection"]["status"] == "needs_review"
    assert context["projection"]["spendable_now_cents"] is None


def test_loader_accepts_fixture_source_but_cli_append_preserves_each_label(captured, tmp_path):
    path = tmp_path / "catalog.json"
    fixture = make_wallet_profile(profile_id="fictional", profile_name="Fictional week", snapshot=make_fixture_wallets()[0])
    write_wallet_profile(path, fixture)
    code, _, _, _ = run(tmp_path, captured, extra=["--mode", "append"])
    assert code == 0
    contexts = load_wallet_catalog(path)
    assert contexts["fictional"]["snapshot"]["source"] == "fixture"
    assert contexts["study-week"]["snapshot"]["source"] == "nessie"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert all("projection" not in item for item in payload["profiles"])
