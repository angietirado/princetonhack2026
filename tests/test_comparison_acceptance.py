"""Validator-owned acceptance ledger, specified before executing the comparison suite.

The arithmetic expectations are hand-calculated from the synthetic records below;
they are deliberately independent of the implementation's aggregation helpers.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from spending_api.agent_tools import SpendingTools
from spending_api.comparison import (
    ComparisonRequest,
    FindingAssertion,
    compare_spending,
    validate_finding_assertion,
)
from spending_api.models import Account, Snapshot, Transaction

PERIODS = {
    "baseline": {"start": "2026-11-01", "end": "2026-11-03"},
    "current": {"start": "2026-11-04", "end": "2026-11-07"},
}


def record(
    identity,
    timestamp,
    cents,
    category,
    merchant_id,
    merchant,
    *,
    kind="purchase",
    status="completed",
):
    return Transaction(
        id=identity,
        account_id="acceptance-account",
        occurred_at=datetime.fromisoformat(timestamp),
        amount_cents=cents,
        category=category,
        merchant_id=merchant_id,
        merchant=merchant,
        kind=kind,
        status=status,
    )


def scoped(rows):
    return SpendingTools(
        Snapshot(
            source="fixture",
            accounts=[
                Account(
                    id="acceptance-account",
                    customer_id="acceptance-customer",
                    nickname="Acceptance",
                    type="Checking",
                    balance_cents=0,
                )
            ],
            transactions=rows,
        )
    )


def independent_ledger():
    return scoped(
        [
            record(
                "b-food-1", "2026-11-01T00:00:00Z", 10000, "groceries", "grocery-1", "Shared label"
            ),
            record(
                "b-food-2", "2026-11-02T10:00:00Z", 4000, "groceries", "grocery-2", "Shared label"
            ),
            record(
                "b-refund",
                "2026-11-03T23:59:59Z",
                2000,
                "groceries",
                "grocery-1",
                "Shared label",
                kind="refund",
            ),
            record("b-dining", "2026-11-02T12:00:00Z", 3000, "dining", "dining-1", "Cafe"),
            record(
                "b-pending",
                "2026-11-02T12:00:00Z",
                5000,
                "dining",
                "dining-1",
                "Cafe",
                status="pending",
            ),
            record(
                "b-transfer",
                "2026-11-02T12:00:00Z",
                9000,
                "other",
                None,
                "Transfer",
                kind="transfer",
            ),
            record(
                "b-utc-boundary", "2026-11-04T00:30:00+01:00", 500, "transport", "edge", "Transit"
            ),
            record(
                "c-food", "2026-11-04T08:00:00Z", 6000, "groceries", "grocery-1", "Shared label"
            ),
            record(
                "c-refund",
                "2026-11-05T08:00:00Z",
                1000,
                "groceries",
                "grocery-1",
                "Shared label",
                kind="refund",
            ),
            record("c-dining", "2026-11-07T23:59:59Z", 10000, "dining", "dining-1", "Cafe"),
            record(
                "c-pending-refund",
                "2026-11-06T12:00:00Z",
                2000,
                "dining",
                "dining-1",
                "Cafe",
                kind="refund",
                status="pending",
            ),
            record(
                "c-deposit", "2026-11-06T12:00:00Z", 7000, "other", None, "Deposit", kind="deposit"
            ),
            record(
                "c-withdrawal",
                "2026-11-06T12:00:00Z",
                4000,
                "other",
                None,
                "Withdrawal",
                kind="withdrawal",
            ),
            record(
                "c-utc-boundary", "2026-11-03T20:30:00-05:00", 700, "transport", "edge", "Transit"
            ),
            record("outside-before", "2026-10-31T23:59:59Z", 90000, "other", "outside", "Outside"),
            record("outside-after", "2026-11-08T00:00:00Z", 80000, "other", "outside", "Outside"),
        ]
    )


def test_acceptance_ledger_exact_money_identity_and_utc_evidence():
    result = compare_spending(independent_ledger(), ComparisonRequest.model_validate(PERIODS))
    assert result.source == "fixture"
    assert result.total.model_dump() == {
        "baseline_cents": 15500,
        "current_cents": 15700,
        "delta_cents": 200,
        "percent_change_bps": 129,
        "percent_status": "defined",
    }
    assert (result.baseline.gross_spending_cents, result.baseline.refunds_cents) == (17500, 2000)
    assert (result.current.gross_spending_cents, result.current.refunds_cents) == (16700, 1000)
    assert (
        result.baseline.observed_count,
        result.baseline.included_count,
        result.baseline.excluded_count,
    ) == (7, 5, 2)
    assert (
        result.current.observed_count,
        result.current.included_count,
        result.current.excluded_count,
    ) == (7, 4, 3)
    assert (result.baseline.duration_days, result.current.duration_days, result.equal_duration) == (
        3,
        4,
        False,
    )
    assert any("unequal durations" in caveat for caveat in result.caveats)
    assert {row.label: row.delta_cents for row in result.categories.rows} == {
        "groceries": -7000,
        "dining": 7000,
        "transport": 200,
    }
    evidence = {row.id: row for row in result.transaction_evidence}
    assert {
        identity: (row.period, row.signed_spending_cents) for identity, row in evidence.items()
    } == {
        "b-food-1": ("baseline", 10000),
        "b-food-2": ("baseline", 4000),
        "b-refund": ("baseline", -2000),
        "b-dining": ("baseline", 3000),
        "b-utc-boundary": ("baseline", 500),
        "c-food": ("current", 6000),
        "c-refund": ("current", -1000),
        "c-dining": ("current", 10000),
        "c-utc-boundary": ("current", 700),
    }
    merchants = {row.key: row for row in result.merchants.rows}
    assert evidence["b-food-1"].merchant_key != evidence["b-food-2"].merchant_key
    assert merchants[evidence["b-food-1"].merchant_key].delta_cents == -3000
    assert merchants[evidence["b-food-2"].merchant_key].delta_cents == -4000
    assert merchants[evidence["b-dining"].merchant_key].delta_cents == 7000
    assert merchants[evidence["b-utc-boundary"].merchant_key].delta_cents == 200
    assert sum(row.label == "Shared label" for row in merchants.values()) == 2
    assert result.findings[0].assertion == "increased"
    assert "$2.00 USD" in result.findings[0].text
    assert result.evidence_summary.eligible_count == result.evidence_summary.returned_count == 9


@pytest.mark.parametrize("negative", [False, True])
def test_acceptance_observed_zero_and_negative_bases_have_no_percentage(negative):
    baseline = [
        record(
            "refund",
            "2026-11-01T00:00:00Z",
            200 if negative else 500,
            "food",
            "m",
            "Store",
            kind="refund",
        )
    ]
    if not negative:
        baseline.append(record("offset", "2026-11-02T00:00:00Z", 500, "food", "m", "Store"))
    current = record(
        "current", "2026-11-04T00:00:00Z", 100 if negative else 1000, "food", "m", "Store"
    )
    result = compare_spending(
        scoped(baseline + [current]), ComparisonRequest.model_validate(PERIODS)
    )
    assert (result.total.baseline_cents, result.total.current_cents, result.total.delta_cents) == (
        (-200, 100, 300) if negative else (0, 1000, 1000)
    )
    assert result.total.percent_change_bps is None
    assert result.total.percent_status == ("negative_baseline" if negative else "zero_baseline")


def test_acceptance_bounded_groups_and_evidence_keep_omitted_money():
    rows = []
    for index in range(15):
        rows.extend(
            [
                record(
                    f"b-{index}",
                    "2026-11-01T00:00:00Z",
                    100 + index,
                    f"category-{index}",
                    f"merchant-{index}",
                    f"Store {index}",
                ),
                record(
                    f"c-{index}",
                    "2026-11-04T00:00:00Z",
                    200 + 2 * index,
                    f"category-{index}",
                    f"merchant-{index}",
                    f"Store {index}",
                ),
            ]
        )
    result = compare_spending(scoped(rows), ComparisonRequest.model_validate(PERIODS))
    assert (result.total.baseline_cents, result.total.current_cents, result.total.delta_cents) == (
        1605,
        3210,
        1605,
    )
    for section in (result.categories, result.merchants):
        assert (len(section.rows), section.total_groups, section.omitted_groups) == (10, 15, 5)
        assert (
            section.omitted_baseline_cents,
            section.omitted_current_cents,
            section.omitted_delta_cents,
        ) == (510, 1020, 510)
        assert sum(row.delta_cents for row in section.rows) == 1095
    assert len(result.transaction_evidence) <= 20
    assert result.evidence_summary.eligible_count == 30
    assert result.evidence_summary.omitted_count == 30 - len(result.transaction_evidence)
    assert result.evidence_summary.truncated
    assert len(result.model_dump_json().encode("utf-8")) <= 24000


def test_acceptance_stable_immutable_findings_reject_forged_proposals():
    data = independent_ledger()
    query = ComparisonRequest.model_validate(PERIODS)
    result = compare_spending(data, query)
    data.snapshot.transactions.reverse()
    assert compare_spending(data, query).model_dump_json() == result.model_dump_json()
    finding = result.findings[0]
    proposal = dict(
        PERIODS,
        comparison_id=result.comparison_id,
        source_hash=result.source_hash,
        calculation_version=result.calculation_version,
        finding_id=finding.finding_id,
        assertion="increased",
    )
    assert validate_finding_assertion(result, proposal) is finding
    for changes in [
        {"assertion": "decreased"},
        {"source_hash": "0" * 64},
        {"finding_id": "0" * 64},
        {"text": "Invented savings"},
        {"total": {"delta_cents": 99999}},
        {"group_delta_cents": 99999},
    ]:
        with pytest.raises(ValueError):
            validate_finding_assertion(result, {**proposal, **changes})
    with pytest.raises(ValidationError):
        result.total.delta_cents = 99999
    with pytest.raises(ValidationError):
        result.findings[0].text = "Invented savings"
    with pytest.raises(ValidationError):
        FindingAssertion.model_validate({**proposal, "customer_id": "other"})


@pytest.mark.parametrize("corruption", ["foreign_account", "mixed_customer"])
def test_acceptance_malformed_scope_is_rejected(corruption):
    data = independent_ledger()
    if corruption == "foreign_account":
        data.snapshot.transactions[0].account_id = "other-account"
    else:
        data.snapshot.accounts.append(
            Account(
                id="other-account",
                customer_id="other-customer",
                nickname="Other",
                type="Checking",
                balance_cents=0,
            )
        )
    with pytest.raises(ValueError):
        compare_spending(data, ComparisonRequest.model_validate(PERIODS))


def test_acceptance_source_and_customer_are_part_of_evidence_identity():
    data = independent_ledger()
    query = ComparisonRequest.model_validate(PERIODS)
    original = compare_spending(data, query)
    data.snapshot.accounts[0].customer_id = "a-different-authorized-customer"
    other_customer = compare_spending(data, query)
    assert other_customer.total == original.total
    assert other_customer.source_hash != original.source_hash
    assert other_customer.comparison_id != original.comparison_id
    data.snapshot.source = "another-synthetic-source"
    other_source = compare_spending(data, query)
    assert other_source.total == original.total
    assert other_source.source_hash != other_customer.source_hash
