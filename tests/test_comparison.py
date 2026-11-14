"""Deterministic comparisons and typed findings; no model calls or credentials."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from spending_api.agent_tools import SpendingTools, bind_snapshot
from spending_api.comparison import (
    CALCULATION_VERSION,
    ComparisonRequest,
    FindingAssertion,
    compare_spending,
    validate_finding_assertion,
)
from spending_api.models import Account, Snapshot, Transaction


def request(**overrides):
    return ComparisonRequest.model_validate(
        {
            "baseline": {"start": "2026-10-01", "end": "2026-10-31"},
            "current": {"start": "2026-11-01", "end": "2026-11-30"},
            **overrides,
        }
    )


def transaction(identity, when, cents, **overrides):
    return Transaction(
        id=identity,
        account_id="a",
        kind=overrides.pop("kind", "purchase"),
        status=overrides.pop("status", "completed"),
        amount_cents=cents,
        occurred_at=datetime.fromisoformat(when),
        merchant=overrides.pop("merchant", "Cafe"),
        merchant_id=overrides.pop("merchant_id", "m1"),
        category=overrides.pop("category", "dining"),
        **overrides,
    )


def tools(*rows):
    return SpendingTools(
        Snapshot(
            source="synthetic-test",
            transactions=list(rows),
            accounts=[
                Account(id="a", customer_id="c", nickname="Test", type="Checking", balance_cents=0)
            ],
        )
    )


def pair(baseline, current):
    rows = []
    for identity, when, value in (
        ("b", "2026-10-01T12:00:00Z", baseline),
        ("c", "2026-11-01T12:00:00Z", current),
    ):
        rows.append(
            transaction(identity, when, abs(value), kind="refund" if value < 0 else "purchase")
        )
    return tools(*rows)


def proposal(result, finding=None, **changes):
    finding = finding or result.findings[0]
    return {
        "comparison_id": result.comparison_id,
        "source_hash": result.source_hash,
        "calculation_version": result.calculation_version,
        "baseline": {"start": result.baseline.start, "end": result.baseline.end},
        "current": {"start": result.current.start, "end": result.current.end},
        "finding_id": finding.finding_id,
        "assertion": finding.assertion,
        **changes,
    }


def test_refunds_exclusions_and_total_reconciliation():
    data = tools(
        transaction("b1", "2026-10-02T00:00:00Z", 2000),
        transaction("b2", "2026-10-03T00:00:00Z", 500, kind="refund"),
        transaction("c1", "2026-11-02T00:00:00Z", 3000),
        transaction("c2", "2026-11-03T00:00:00Z", 100, kind="fee", category="fees"),
        transaction("c3", "2026-11-04T00:00:00Z", 700, status="pending"),
        transaction("c4", "2026-11-04T00:00:00Z", 900, kind="transfer"),
        transaction("c5", "2026-11-04T00:00:00Z", 1000, kind="deposit"),
        transaction("c6", "2026-11-04T00:00:00Z", 1100, kind="withdrawal"),
        transaction("old", "2026-09-30T00:00:00Z", 9999),
    )
    result = compare_spending(data, request())
    assert result.total.model_dump() == {
        "baseline_cents": 1500,
        "current_cents": 3100,
        "delta_cents": 1600,
        "percent_change_bps": 10667,
        "percent_status": "defined",
    }
    assert result.baseline.gross_spending_cents == 2000 and result.baseline.refunds_cents == 500
    assert result.current.observed_count == 6 and result.current.included_count == 2
    assert result.current.excluded_count == 4
    assert (
        sum(
            row.signed_spending_cents
            for row in result.transaction_evidence
            if row.period == "baseline"
        )
        == 1500
    )
    assert {row.id for row in result.transaction_evidence} == {"b1", "b2", "c1", "c2"}
    for section in (result.categories, result.merchants):
        assert sum(row.delta_cents for row in section.rows) == result.total.delta_cents
    assert result.equal_duration is False
    assert any("unequal durations" in text for text in result.caveats)


@pytest.mark.parametrize(
    "baseline,current,bps,status",
    [
        (0, 0, None, "zero_baseline"),
        (0, 100, None, "zero_baseline"),
        (-100, 100, None, "negative_baseline"),
        (-100, -200, None, "negative_baseline"),
        (100, 0, -10000, "defined"),
        (100, -100, -20000, "defined"),
        (20000, 20001, 1, "defined"),
        (20000, 19999, -1, "defined"),
    ],
)
def test_percentages_have_explicit_baselines_and_half_up_rounding(baseline, current, bps, status):
    result = compare_spending(pair(baseline, current), request())
    assert result.total.delta_cents == current - baseline
    assert result.total.percent_change_bps == bps and result.total.percent_status == status
    assert type(result.total.delta_cents) is int


def test_empty_observed_period_is_not_claimed_complete():
    result = compare_spending(tools(), request())
    assert result.total.delta_cents == 0 and result.total.percent_change_bps is None
    assert result.baseline.included_count == result.current.included_count == 0
    assert result.coverage == "observed_records_only"
    assert "completeness" in result.caveats[0]
    assert result.findings[0].assertion == "unchanged"
    assert "Observed records only" in result.findings[0].text


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"baseline": {"start": "2026-10-01"}},
        {"customer_id": "other"},
        {"baseline": {"start": "2026-11-01", "end": "2026-11-02"}},
        {"baseline": {"start": "2026-10-31", "end": "2026-10-01"}},
        {"baseline": {"start": "2026-10-01", "end": "2026-11-01"}},
        {"baseline": {"start": 0, "end": "2026-10-31"}},
        {"baseline": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31"}},
        {"baseline": {"start": "2026-10-01", "end": "2026-10-31", "source": "file"}},
        {"current": {"start": "2026-11-31", "end": "2026-12-01"}},
    ],
)
def test_request_requires_two_explicit_strict_ordered_periods(value):
    with pytest.raises(ValidationError):
        if not value:
            ComparisonRequest.model_validate(value)
        else:
            request(**value)


def test_utc_boundaries_and_equal_duration():
    data = tools(
        transaction("boundary", "2026-10-31T23:30:00-02:00", 1234),
        transaction("prior", "2026-11-01T00:30:00+02:00", 500),
        transaction("outside", "2026-11-02T00:00:00Z", 999),
    )
    query = request(
        baseline={"start": "2026-10-31", "end": "2026-10-31"},
        current={"start": "2026-11-01", "end": "2026-11-01"},
    )
    result = compare_spending(data, query)
    assert result.total.baseline_cents == 500 and result.total.current_cents == 1234
    assert result.equal_duration and result.current.duration_days == 1
    assert all(
        row.occurred_at.utcoffset().total_seconds() == 0 for row in result.transaction_evidence
    )
    assert data.snapshot.transactions[0].occurred_at.utcoffset().total_seconds() == -7200


@pytest.mark.parametrize("change", ["duplicate", "customer", "foreign", "blank", "naive", "money"])
def test_scope_and_identity_validation_precedes_comparison(change):
    data = pair(100, 200)
    if change == "duplicate":
        data.snapshot.transactions[1].id = "b"
    elif change == "customer":
        data.snapshot.accounts.append(
            Account(id="b", customer_id="other", nickname="", type="Checking", balance_cents=0)
        )
    elif change == "foreign":
        data.snapshot.transactions[0].account_id = "foreign"
    elif change == "blank":
        data.snapshot.accounts[0].customer_id = " "
    elif change == "naive":
        data.snapshot.transactions[0].occurred_at = datetime(2026, 10, 1)
    else:
        data.snapshot.transactions[0].amount_cents = True
    with pytest.raises(ValueError):
        compare_spending(data, request())


def test_merchant_ids_and_unknown_bucket_preserve_identity():
    data = tools(
        transaction("b1", "2026-10-01T00:00:00Z", 100, merchant="Same", merchant_id="one"),
        transaction("b2", "2026-10-01T00:00:00Z", 200, merchant="Same", merchant_id="two"),
        transaction("b3", "2026-10-01T00:00:00Z", 300, merchant="Same", merchant_id=None),
        transaction("c1", "2026-11-01T00:00:00Z", 400, merchant="Renamed", merchant_id="one"),
        transaction("c2", "2026-11-01T00:00:00Z", 500, merchant="Different", merchant_id=""),
    )
    result = compare_spending(data, request())
    assert result.merchants.total_groups == 3
    renamed = next(row for row in result.merchants.rows if row.label == "Renamed")
    assert (renamed.baseline_cents, renamed.current_cents) == (100, 400)
    unknown = next(row for row in result.merchants.rows if row.key == "unknown_merchant")
    assert (unknown.baseline_cents, unknown.current_cents, unknown.identity_kind) == (
        300,
        500,
        "unidentified",
    )


def test_hashes_are_stable_for_reordered_and_timezone_equivalent_records():
    first = pair(100, 200)
    second = SpendingTools(first.snapshot.model_copy(deep=True))
    second.snapshot.transactions.reverse()
    second.snapshot.transactions[0].occurred_at = datetime.fromisoformat(
        "2026-11-01T07:00:00-05:00"
    )
    a, b = compare_spending(first, request()), compare_spending(second, request())
    assert a.model_dump() == b.model_dump()
    assert a.source_hash == bind_snapshot(first)[1]
    assert a.calculation_version == CALCULATION_VERSION
    changed = compare_spending(first, request(current={"start": "2026-11-01", "end": "2026-11-29"}))
    assert changed.source_hash == a.source_hash and changed.comparison_id != a.comparison_id
    second.snapshot.transactions[0].amount_cents += 1
    assert compare_spending(second, request()).source_hash != a.source_hash


def test_large_group_set_and_long_labels_stay_bounded_and_reconcile():
    rows = [
        transaction(
            f"tx-{index:03}",
            "2026-11-01T00:00:00Z",
            100 + index,
            category=f"{index}-" + "🪙" * 100,
            merchant=f"{index}-" + '"' * 300,
            merchant_id=f"merchant-{index}",
        )
        for index in range(45)
    ]
    rows.append(
        transaction(
            "decrease", "2026-10-01T00:00:00Z", 1, category="small decrease", merchant_id="decrease"
        )
    )
    result = compare_spending(tools(*rows), request())
    assert len(result.model_dump_json().encode()) <= 24_000
    assert len(result.transaction_evidence) <= 20
    assert result.evidence_summary.eligible_count == 46
    assert result.evidence_summary.omitted_count == 46 - len(result.transaction_evidence)
    assert result.evidence_summary.baseline_returned == 1 and result.evidence_summary.truncated
    for section in (result.categories, result.merchants):
        assert len(section.rows) == 10 and section.omitted_groups == 36
        assert (
            sum(row.baseline_cents for row in section.rows) + section.omitted_baseline_cents
            == result.total.baseline_cents
        )
        assert (
            sum(row.current_cents for row in section.rows) + section.omitted_current_cents
            == result.total.current_cents
        )
        assert (
            sum(row.delta_cents for row in section.rows) + section.omitted_delta_cents
            == result.total.delta_cents
        )
        assert any(row.delta_cents == -1 for row in section.rows)
        assert all(len(row.label.encode()) <= 64 for row in section.rows)
    assert len(result.findings) == 5


def test_oversized_transaction_identity_is_omitted_never_truncated():
    result = compare_spending(tools(transaction("x" * 500, "2026-11-01T00:00:00Z", 100)), request())
    assert result.total.current_cents == 100 and result.transaction_evidence == ()
    assert result.evidence_summary.omitted_count == 1


def test_generated_findings_are_immutable_and_validator_returns_trusted_text():
    result = compare_spending(pair(100, 200), request())
    finding = validate_finding_assertion(result, proposal(result))
    assert finding is result.findings[0]
    assert "$1.00 USD" in finding.text and "$2.00 USD" in finding.text
    with pytest.raises(ValidationError):
        finding.text = "Forged $999"
    with pytest.raises(ValidationError):
        FindingAssertion.model_validate(proposal(result, text="Forged $999"))


@pytest.mark.parametrize(
    "change",
    [
        {"comparison_id": "a" * 64},
        {"source_hash": "b" * 64},
        {"finding_id": "c" * 64},
        {"assertion": "decreased"},
        {"assertion": "largest_increase"},
        {"baseline": {"start": "2026-10-02", "end": "2026-10-31"}},
        {"current": {"start": "2026-11-01", "end": "2026-11-29"}},
        {"calculation_version": "other"},
    ],
)
def test_forged_finding_identity_direction_and_rank_are_rejected(change):
    result = compare_spending(pair(100, 200), request())
    with pytest.raises(ValueError):
        validate_finding_assertion(result, proposal(result, **change))


def test_rank_ties_include_omitted_groups():
    rows = [
        transaction(f"increase-{i}", "2026-11-01T00:00:00Z", 1000 + i, category=f"up{i}")
        for i in range(10)
    ]
    rows.extend(
        transaction(f"decrease-{i}", "2026-10-01T00:00:00Z", 1, category=f"down{i}")
        for i in range(2)
    )
    result = compare_spending(tools(*rows), request())
    finding = next(
        row
        for row in result.findings
        if row.scope == "category" and row.assertion == "largest_decrease"
    )
    assert finding.joint_largest
    assert validate_finding_assertion(result, proposal(result, finding)) is finding
