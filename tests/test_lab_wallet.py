"""Wallet estimates use checking cash, explicit schedules, and immutable local branches."""

import copy

import pytest

from spending_api.lab_wallet import (
    WalletDataError,
    freeze_wallet,
    make_fixture_wallets,
    project_wallet,
    validate_wallet,
)


@pytest.fixture
def wallet():
    return make_fixture_wallets()[0]


def rewrite(wallet, update):
    value = copy.deepcopy(wallet)
    update(value)
    return freeze_wallet(value)


def checking(wallet):
    return next(account for account in wallet["accounts"] if account["cash_eligible"])


def payment(wallet, *, identity="payment", status="pending", amount=3000, kind="bill_payment"):
    bill = wallet["obligations"][0]
    return {
        "id": identity, "kind": kind, "status": status, "medium": "balance",
        "amount_cents": amount, "created_at": wallet["captured_at"],
        "payer_id": bill["account_id"], "payee_id": None, "bill_id": bill["id"],
        "parent_transaction_id": None,
    }


def test_fixtures_are_explicit_fiction_and_stable():
    first, second = make_fixture_wallets(), make_fixture_wallets()
    assert first == second
    assert [item["available_cents"] for item in first] == [40000, 14000, 60000]
    assert len({item["id"] for item in first}) == 3
    assert all(item["source"] == "fixture" for item in first)
    assert all(any("not fetched from Nessie" in note for note in item["assumptions"]) for item in first)


def test_holds_are_deducted_once_and_credit_is_not_cash(wallet):
    result = project_wallet(wallet)
    assert result["baseline_available_cents"] == 40000
    assert result["scheduled_obligations_cents"] == 18000
    assert result["ending_available_cents"] == 22000
    assert result["shopping_allowance_cents"] == 22000
    assert result["spendable_now_cents"] == 22000
    assert all(entry["kind"] != "purchase" for entry in result["timeline"])


def test_explicit_income_reserve_and_local_decisions(wallet):
    income = [{"id": "pay", "label": "Explicit payday", "amount_cents": 5000, "available_at": "2026-11-18"}]
    decision = [{"id": "trip", "kind": "shopping", "label": "Chosen basket",
                 "amount_cents": 1500, "occurred_at": "2026-11-15"}]
    before = copy.deepcopy((wallet, income, decision))
    result = project_wallet(wallet, reserve_cents=3000, expected_income=income, decisions=decision)
    assert result["ending_available_cents"] == 25500
    assert result["shopping_allowance_cents"] == 22500
    assert result["spendable_now_cents"] == 17500
    assert result["local_spending_cents"] == 1500
    assert (wallet, income, decision) == before
    assert result == project_wallet(wallet, reserve_cents=3000, expected_income=income, decisions=decision)


def test_delayed_income_exposes_timing_shortfall_even_when_week_end_same(wallet):
    wallet = rewrite(wallet, lambda data: data["obligations"][0].update(amount_cents=55000))
    income = [{"id": "pay", "label": "Explicit pay", "amount_cents": 30000, "available_at": "2026-11-15"}]
    normal = project_wallet(wallet, expected_income=income)
    delayed = project_wallet(wallet, expected_income=income, events=[
        {"kind": "delay_income", "income_id": "pay", "delay_days": 3},
    ])
    assert normal["ending_available_cents"] == delayed["ending_available_cents"] == 15000
    assert normal["shortfall_cents"] == 0
    assert normal["spendable_now_cents"] == 15000
    assert delayed["shortfall_cents"] == 15000
    assert delayed["spendable_now_cents"] == 0
    assert normal["id"] != delayed["id"]


def test_income_delayed_outside_window_is_not_spendable(wallet):
    income = [{"id": "pay", "label": "Explicit pay", "amount_cents": 5000, "available_at": "2026-11-20"}]
    result = project_wallet(wallet, expected_income=income, events=[
        {"kind": "delay_income", "income_id": "pay", "delay_days": 2},
    ])
    assert result["expected_income_cents"] == 0
    assert result["deferred_income_cents"] == 5000
    assert result["ending_available_cents"] == 22000


def test_car_unavailability_changes_modes_without_inventing_expenses(wallet):
    baseline = project_wallet(wallet)
    result = project_wallet(wallet, events=[{"kind": "car_unavailable"}])
    assert result["allowed_travel_modes"] == ["walk"]
    assert result["ending_available_cents"] == baseline["ending_available_cents"]
    assert result["local_spending_cents"] == 0


def test_deposits_and_pending_credits_never_become_expected_income(wallet):
    def add(data):
        for status in ("pending", "completed"):
            tx = payment(data, identity=f"deposit-{status}", status=status, kind="deposit", amount=999999)
            tx["bill_id"] = None
            data["transactions"].append(tx)
    result = project_wallet(rewrite(wallet, add))
    assert result["expected_income_cents"] == 0
    assert result["ending_available_cents"] == 22000


def test_overdrawn_checking_reduces_cash_instead_of_being_ignored(wallet):
    def add(data):
        data["accounts"].append({
            "id": "other-checking", "type": "Checking", "nickname": "Other checking",
            "balance_cents": -5000, "pending_debits_cents": 0,
            "available_cents": -5000, "cash_eligible": True,
        })
        data["available_cents"] -= 5000
    result = project_wallet(rewrite(wallet, add))
    assert result["baseline_available_cents"] == 35000
    assert result["shopping_allowance_cents"] == 17000


def test_pending_one_off_bill_payment_is_not_reserved_twice(wallet):
    def add(data):
        account = checking(data)
        account["pending_debits_cents"] += 3000
        account["available_cents"] -= 3000
        data["available_cents"] -= 3000
        data["transactions"].append(payment(data))
    result = project_wallet(rewrite(wallet, add))
    assert result["status"] == "ready"
    assert result["baseline_available_cents"] == 37000
    assert result["already_held_bill_payments_cents"] == 3000
    assert result["scheduled_obligations_cents"] == 15000
    assert result["ending_available_cents"] == 22000


def test_completed_one_off_payment_is_not_subtracted_again(wallet):
    def add(data):
        account = checking(data)
        account["balance_cents"] -= 5000
        account["available_cents"] -= 5000
        data["available_cents"] -= 5000
        data["transactions"].append(payment(data, status="completed", amount=5000))
    result = project_wallet(rewrite(wallet, add))
    assert result["already_paid_bill_payments_cents"] == 5000
    assert result["scheduled_obligations_cents"] == 13000
    assert result["ending_available_cents"] == 22000


@pytest.mark.parametrize("status", ["declined", "cancelled"])
def test_unsettled_failed_bill_attempt_does_not_pay_obligation(wallet, status):
    value = rewrite(wallet, lambda data: data["transactions"].append(payment(data, status=status)))
    assert project_wallet(value)["scheduled_obligations_cents"] == 18000


def test_hold_mismatch_blocks_confident_allowance(wallet):
    value = rewrite(wallet, lambda data: data["transactions"].append(payment(data)))
    result = project_wallet(value)
    assert result["status"] == "needs_review"
    assert result["shopping_allowance_cents"] is None
    assert result["spendable_now_cents"] is None
    assert result["ending_available_cents"] is None
    assert result["unresolved"][0]["code"] == "pending_hold_mismatch"


def test_recurring_payment_has_no_invented_occurrence_identity(wallet):
    def change(data):
        data["obligations"][0]["status"] = "recurring"
        data["obligations"][0]["recurring_day"] = 16
        data["transactions"].append(payment(data, status="completed"))
    result = project_wallet(rewrite(wallet, change))
    assert result["status"] == "needs_review"
    assert result["unresolved"][0]["code"] == "recurring_payment_occurrence_unknown"


def test_bill_payment_reversal_requires_review(wallet):
    def add(data):
        data["transactions"].append(payment(data, status="completed"))
        reversal = payment(data, identity="reversal", status="completed", kind="refund")
        reversal["parent_transaction_id"] = "payment"
        reversal["bill_id"] = None
        data["transactions"].append(reversal)
    result = project_wallet(rewrite(wallet, add))
    assert result["unresolved"][0]["code"] == "bill_payment_reversal"


def test_unknown_date_is_unknown_even_with_recurring_day(wallet):
    def change(data):
        data["obligations"][0].update(status="recurring", recurring_day=16, due_at=None)
    result = project_wallet(rewrite(wallet, change))
    assert result["status"] == "needs_review"
    assert result["shopping_allowance_cents"] is None
    assert result["unresolved"][0]["code"] == "bill_due_date_unknown"


@pytest.mark.parametrize("status", ["cancelled", "completed"])
def test_inactive_bills_are_not_future_obligations(wallet, status):
    value = rewrite(wallet, lambda data: data["obligations"][0].update(status=status))
    result = project_wallet(value)
    assert result["scheduled_obligations_cents"] == 0
    assert result["shopping_allowance_cents"] == 40000


def test_past_due_bill_reserved_at_capture_and_dates_are_utc(wallet):
    value = rewrite(wallet, lambda data: data["obligations"][0].update(due_at="2026-11-13T23:00:00-05:00"))
    result = project_wallet(value)
    assert result["timeline"][0]["at"] == "2026-11-14T04:00:00Z"
    overdue = rewrite(wallet, lambda data: data["obligations"][0].update(due_at="2026-11-10"))
    assert project_wallet(overdue)["timeline"][0]["at"] == wallet["captured_at"]


def test_fractional_seconds_are_sorted_chronologically(wallet):
    value = rewrite(wallet, lambda data: data["obligations"][0].update(
        due_at="2026-11-15T00:00:00.500000Z", amount_cents=60000,
    ))
    result = project_wallet(value, expected_income=[{
        "id": "pay", "label": "Explicit pay", "amount_cents": 50000,
        "available_at": "2026-11-15T00:00:00Z",
    }])
    assert result["timeline"][0]["kind"] == "expected_income"
    assert result["shortfall_cents"] == 0


def test_equal_timestamp_outflows_precede_income(wallet):
    value = rewrite(wallet, lambda data: data["obligations"][0].update(amount_cents=50000))
    result = project_wallet(value, expected_income=[{
        "id": "pay", "label": "Explicit pay", "amount_cents": 30000,
        "available_at": "2026-11-16T00:00:00Z",
    }])
    assert result["timeline"][0]["kind"] == "obligation"
    assert result["shortfall_cents"] == 10000
    assert result["ending_available_cents"] == 20000


def test_window_end_is_exclusive(wallet):
    value = rewrite(wallet, lambda data: data["obligations"][0].update(due_at="2026-11-21"))
    result = project_wallet(value, expected_income=[{
        "id": "pay", "label": "Later pay", "amount_cents": 5000, "available_at": "2026-11-21",
    }])
    assert result["scheduled_obligations_cents"] == result["expected_income_cents"] == 0
    assert result["ending_available_cents"] == 40000


def test_snapshot_tampering_and_invalid_cash_basis_are_rejected(wallet):
    changed = copy.deepcopy(wallet)
    changed["source"] = "nessie"
    with pytest.raises(WalletDataError, match="fingerprint"):
        project_wallet(changed)
    changed = copy.deepcopy(wallet)
    checking(changed)["available_cents"] += 1
    changed["available_cents"] += 1
    with pytest.raises(WalletDataError):
        freeze_wallet(changed)
    assert validate_wallet(wallet) == wallet


@pytest.mark.parametrize("arguments", [
    {"days": 0}, {"days": True}, {"reserve_cents": -1}, {"reserve_cents": 2.5},
    {"expected_income": {}},
    {"events": [{"kind": "delay_income", "income_id": "unknown", "delay_days": 1}]},
    {"events": [{"kind": "car_unavailable", "delay_days": 2}]},
    {"expected_income": [{"id": "pay", "label": "Pay", "amount_cents": True, "available_at": "2026-11-15"}]},
    {"expected_income": [{"id": "pay", "label": "Pay", "amount_cents": 100, "available_at": "2026-11-15T12:00:00"}]},
    {"expected_income": [{"id": "pay", "label": "Pay", "amount_cents": 100, "available_at": "2026-11-13"}]},
])
def test_invalid_projection_inputs_fail_closed(wallet, arguments):
    with pytest.raises(WalletDataError):
        project_wallet(wallet, **arguments)



@pytest.mark.parametrize("status", ["pending", "completed"])
def test_bill_payment_without_identifier_blocks_ambiguous_cash_obligations(wallet, status):
    def add(data):
        transaction = payment(data, status=status)
        transaction["bill_id"] = None
        data["transactions"].append(transaction)
        account = checking(data)
        if status == "pending":
            account["pending_debits_cents"] += 3000
        else:
            account["balance_cents"] -= 3000
        account["available_cents"] -= 3000
        data["available_cents"] -= 3000
    result = project_wallet(rewrite(wallet, add))
    assert result["status"] == "needs_review"
    assert result["shopping_allowance_cents"] is None
    assert result["spendable_now_cents"] is None
    assert result["ending_available_cents"] is None
    assert result["unresolved"][0]["code"] == "bill_payment_link_unknown"
    assert result["scheduled_obligations_cents"] == 0  # Do not pretend an ambiguous remainder is known.


@pytest.mark.parametrize("status", ["declined", "cancelled"])
def test_failed_unlinked_bill_payment_does_not_block_allowance(wallet, status):
    def add(data):
        transaction = payment(data, status=status)
        transaction["bill_id"] = None
        data["transactions"].append(transaction)
    result = project_wallet(rewrite(wallet, add))
    assert result["status"] == "ready"


def test_unlinked_payment_before_bill_existed_does_not_block_new_bill(wallet):
    def add(data):
        transaction = payment(data, status="completed")
        transaction.update(bill_id=None, created_at="2026-11-13T00:00:00Z")
        data["transactions"].append(transaction)
    assert project_wallet(rewrite(wallet, add))["status"] == "ready"


def test_unlinked_payment_without_in_window_cash_obligations_is_context_only(wallet):
    def add(data):
        data["obligations"][0]["due_at"] = "2026-12-01T00:00:00Z"
        transaction = payment(data, status="completed")
        transaction["bill_id"] = None
        data["transactions"].append(transaction)
    assert project_wallet(rewrite(wallet, add))["status"] == "ready"


def test_pooling_assumption_is_explicit(wallet):
    result = project_wallet(wallet)
    assert any("Checking balances are pooled" in note for note in result["assumptions"])
    assert any("individual bill-paying account" in note for note in result["assumptions"])

