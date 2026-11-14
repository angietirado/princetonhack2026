"""Deterministic, bounded period comparisons and server-rendered financial findings."""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from spending_api.agent_tools import SPENDING_KINDS, SpendingTools, bind_snapshot

CALCULATION_VERSION = "spending-comparison-v1"
MAX_COMPARISON_BYTES = 24_000
MAX_GROUPS = 10
MAX_EVIDENCE = 20
Integer = Annotated[int, Field(strict=True)]
Count = Annotated[int, Field(strict=True, ge=0)]
Assertion = Literal["increased", "decreased", "unchanged", "largest_increase", "largest_decrease"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class ComparisonWindow(FrozenModel):
    start: date
    end: date

    @field_validator("start", "end", mode="before")
    @classmethod
    def iso_calendar_date(cls, value):
        if type(value) is date:
            return value
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Use an explicit ISO calendar date")
        return date.fromisoformat(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.start > self.end:
            raise ValueError("Start must be on or before end")
        return self


class ComparisonRequest(FrozenModel):
    baseline: ComparisonWindow
    current: ComparisonWindow

    @model_validator(mode="after")
    def distinct_periods(self):
        if self.baseline.end >= self.current.start:
            raise ValueError("Baseline must end before the current period starts")
        return self


class PeriodStats(ComparisonWindow):
    duration_days: Count
    observed_count: Count
    included_count: Count
    excluded_count: Count
    gross_spending_cents: Count
    refunds_cents: Count
    net_spending_cents: Integer


class SpendingChange(FrozenModel):
    baseline_cents: Integer
    current_cents: Integer
    delta_cents: Integer
    percent_change_bps: Integer | None
    percent_status: Literal["defined", "zero_baseline", "negative_baseline"]


class GroupChange(SpendingChange):
    key: str
    label: str
    label_truncated: bool
    identity_kind: Literal["category", "merchant_id", "unidentified"]


class GroupSection(FrozenModel):
    rows: tuple[GroupChange, ...] = Field(max_length=MAX_GROUPS)
    total_groups: Count
    omitted_groups: Count
    omitted_baseline_cents: Integer
    omitted_current_cents: Integer
    omitted_delta_cents: Integer


class ComparisonFinding(FrozenModel):
    finding_id: str
    scope: Literal["total", "category", "merchant"]
    group_key: str | None
    assertion: Assertion
    joint_largest: bool
    text: str


class TransactionEvidence(FrozenModel):
    id: str
    period: Literal["baseline", "current"]
    occurred_at: datetime
    category_key: str
    merchant_key: str
    signed_spending_cents: Integer


class EvidenceSummary(FrozenModel):
    eligible_count: Count
    returned_count: Count
    omitted_count: Count
    baseline_returned: Count
    current_returned: Count
    truncated: bool


class SpendingComparison(FrozenModel):
    status: Literal["ok"] = "ok"
    comparison_id: str
    calculation_version: Literal["spending-comparison-v1"] = CALCULATION_VERSION
    source: str
    source_hash: str
    currency: Literal["USD"] = "USD"
    coverage: Literal["observed_records_only"] = "observed_records_only"
    baseline: PeriodStats
    current: PeriodStats
    equal_duration: bool
    total: SpendingChange
    categories: GroupSection
    merchants: GroupSection
    findings: tuple[ComparisonFinding, ...] = Field(max_length=5)
    transaction_evidence: tuple[TransactionEvidence, ...] = Field(max_length=MAX_EVIDENCE)
    evidence_summary: EvidenceSummary
    caveats: tuple[str, ...]


class FindingAssertion(ComparisonRequest):
    comparison_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    calculation_version: Literal["spending-comparison-v1"]
    finding_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    assertion: Assertion


def _hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _label(value: str) -> tuple[str, bool]:
    # Bound UTF-8 bytes, not just characters; do not alter identity keys.
    clean = " ".join(value.split())
    encoded = clean.encode()
    if len(encoded) <= 64:
        return clean, False
    return encoded[:61].decode(errors="ignore") + "…", True


def _change(baseline: int, current: int) -> SpendingChange:
    delta = current - baseline
    bps = None
    if baseline > 0:
        with localcontext() as context:
            context.prec = max(28, len(str(abs(delta))) + len(str(baseline)) + 8)
            bps = int(
                (Decimal(delta) * 10_000 / Decimal(baseline)).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            )
    return SpendingChange(
        baseline_cents=baseline,
        current_cents=current,
        delta_cents=delta,
        percent_change_bps=bps,
        percent_status="defined"
        if baseline > 0
        else "zero_baseline"
        if baseline == 0
        else "negative_baseline",
    )


def _category_key(tx) -> str:
    return _hash(["category", tx.category])


def _merchant_key(tx) -> str:
    return (
        _hash(["merchant", tx.merchant_id])
        if tx.merchant_id and tx.merchant_id.strip()
        else "unknown_merchant"
    )


def _included(tx) -> bool:
    return tx.status == "completed" and tx.kind in SPENDING_KINDS | {"refund"}


def _signed(tx) -> int:
    return -tx.amount_cents if tx.kind == "refund" else tx.amount_cents


def _section(groups: dict) -> GroupSection:
    def delta(item):
        return item[1]["current"] - item[1]["baseline"]

    selected = heapq.nsmallest(
        MAX_GROUPS, groups.items(), key=lambda item: (-abs(delta(item)), item[0])
    )
    # Keep both directional extremes visible even if ten large increases would
    # otherwise hide the largest decrease (or vice versa).
    extremes = []
    for sign in (1, -1):
        extreme = min(
            (item for item in groups.items() if sign * delta(item) > 0),
            key=lambda item: (-sign * delta(item), item[0]),
            default=None,
        )
        if extreme is not None:
            extremes.append(extreme)
    protected = {item[0] for item in extremes}
    for row in extremes:
        if row not in selected:
            index = next(
                i for i in range(len(selected) - 1, -1, -1) if selected[i][0] not in protected
            )
            selected[index] = row
    selected.sort(key=lambda item: (-abs(delta(item)), item[0]))
    keys = {item[0] for item in selected}
    omitted_baseline = sum(value["baseline"] for key, value in groups.items() if key not in keys)
    omitted_current = sum(value["current"] for key, value in groups.items() if key not in keys)
    rows = tuple(
        GroupChange(
            key=key,
            label=_label(value["label"])[0],
            label_truncated=_label(value["label"])[1],
            identity_kind=value["identity_kind"],
            **_change(value["baseline"], value["current"]).model_dump(),
        )
        for key, value in selected
    )
    return GroupSection(
        rows=rows,
        total_groups=len(groups),
        omitted_groups=len(groups) - len(rows),
        omitted_baseline_cents=omitted_baseline,
        omitted_current_cents=omitted_current,
        omitted_delta_cents=omitted_current - omitted_baseline,
    )


def _money(cents: int) -> str:
    value = abs(cents)
    return f"{'-' if cents < 0 else ''}${value // 100:,}.{value % 100:02} USD"


def _findings(
    comparison_id, request, total, categories, merchants, category_groups, merchant_groups
):
    periods = (
        f"baseline {request.baseline.start}–{request.baseline.end} UTC"
        f" and current {request.current.start}–{request.current.end} UTC"
    )

    def make(scope, key, assertion, change, title, joint=False):
        direction = "increased" if change.delta_cents > 0 else "decreased"
        fact = (
            f"was unchanged at {_money(change.current_cents)}"
            if change.delta_cents == 0
            else f"{direction} by {_money(abs(change.delta_cents))}, "
            f"from {_money(change.baseline_cents)} to {_money(change.current_cents)}"
        )
        return ComparisonFinding(
            finding_id=_hash([comparison_id, scope, key, assertion]),
            scope=scope,
            group_key=key,
            assertion=assertion,
            joint_largest=joint,
            text=f"{title} {fact} between {periods}. Observed records only.",
        )

    direction = (
        "increased"
        if total.delta_cents > 0
        else "decreased"
        if total.delta_cents < 0
        else "unchanged"
    )
    findings = [make("total", None, direction, total, "Observed net spending")]
    for scope, section, groups in (
        ("category", categories, category_groups),
        ("merchant", merchants, merchant_groups),
    ):
        for sign, assertion in ((1, "largest_increase"), (-1, "largest_decrease")):
            candidates = [row for row in section.rows if sign * row.delta_cents > 0]
            if not candidates:
                continue
            row = min(candidates, key=lambda item: (-sign * item.delta_cents, item.key))
            joint = (
                sum(
                    item["current"] - item["baseline"] == row.delta_cents
                    for item in groups.values()
                )
                > 1
            )
            label = json.dumps(row.label, ensure_ascii=False)
            title = (
                f"{scope.capitalize()} {label} "
                f"({'joint-largest' if joint else 'largest'} observed "
                f"{'increase' if sign > 0 else 'decrease'})"
            )
            findings.append(make(scope, row.key, assertion, row, title, joint))
    return tuple(findings)


def _evidence_summary(rows, eligible):
    return EvidenceSummary(
        eligible_count=eligible,
        returned_count=len(rows),
        omitted_count=eligible - len(rows),
        baseline_returned=sum(row.period == "baseline" for row in rows),
        current_returned=sum(row.period == "current" for row in rows),
        truncated=len(rows) < eligible,
    )


def compare_spending(tools: SpendingTools, request: ComparisonRequest) -> SpendingComparison:
    """Compare exactly two requested windows of one authorized, normalized snapshot."""
    request = ComparisonRequest.model_validate(request)
    bound, source_hash = bind_snapshot(tools)
    return compare_bound_spending(bound, request, source_hash)


def compare_bound_spending(
    bound: SpendingTools, request: ComparisonRequest, source_hash: str
) -> SpendingComparison:
    """Internal read-tool path: caller already used bind_snapshot for this request."""
    request = ComparisonRequest.model_validate(request)
    stats = {}
    categories, merchants = {}, {}
    for period in ("baseline", "current"):
        window = getattr(request, period)
        stats[period] = dict(
            start=window.start,
            end=window.end,
            duration_days=(window.end - window.start).days + 1,
            observed_count=0,
            included_count=0,
            excluded_count=0,
            gross_spending_cents=0,
            refunds_cents=0,
            net_spending_cents=0,
        )
    for tx in bound.snapshot.transactions:
        day = tx.occurred_at.date()
        period = next(
            (name for name in stats if stats[name]["start"] <= day <= stats[name]["end"]), None
        )
        if period is None:
            continue
        values = stats[period]
        values["observed_count"] += 1
        if not _included(tx):
            values["excluded_count"] += 1
            continue
        values["included_count"] += 1
        values["refunds_cents" if tx.kind == "refund" else "gross_spending_cents"] += (
            tx.amount_cents
        )
        signed = _signed(tx)
        values["net_spending_cents"] += signed
        for groups, key, label, identity in (
            (categories, _category_key(tx), tx.category or "Uncategorized", "category"),
            (merchants, _merchant_key(tx), tx.merchant, "merchant_id"),
        ):
            if key == "unknown_merchant":
                label, identity = "Unidentified merchants", "unidentified"
            group = groups.setdefault(
                key,
                {
                    "label": label,
                    "identity_kind": identity,
                    "baseline": 0,
                    "current": 0,
                    "label_order": (tx.occurred_at, tx.id),
                },
            )
            group[period] += signed
            if (tx.occurred_at, tx.id) > group["label_order"]:
                group["label"], group["label_order"] = label, (tx.occurred_at, tx.id)
    baseline, current = (PeriodStats(**stats[name]) for name in ("baseline", "current"))
    total = _change(baseline.net_spending_cents, current.net_spending_cents)
    category_section, merchant_section = _section(categories), _section(merchants)
    comparison_id = _hash([CALCULATION_VERSION, source_hash, request.model_dump(mode="json")])
    eligible = baseline.included_count + current.included_count
    candidates = {}
    for period in stats:
        candidates[period] = heapq.nsmallest(
            MAX_EVIDENCE,
            (
                tx
                for tx in bound.snapshot.transactions
                if _included(tx)
                and stats[period]["start"] <= tx.occurred_at.date() <= stats[period]["end"]
                and len(tx.id.encode()) <= 128
            ),
            key=lambda tx: (tx.occurred_at, tx.id),
        )
    selected = [(period, tx) for period in stats for tx in candidates[period][:10]]
    selected.extend((period, tx) for period in stats for tx in candidates[period][10:])
    evidence = tuple(
        TransactionEvidence(
            id=tx.id,
            period=period,
            occurred_at=tx.occurred_at,
            category_key=_category_key(tx),
            merchant_key=_merchant_key(tx),
            signed_spending_cents=_signed(tx),
        )
        for period, tx in selected[:MAX_EVIDENCE]
    )
    caveats = [
        "Observed records only; completeness and freshness are unknown. "
        "Missing records do not prove zero spending.",
        "Completed expenses minus refunds; pending transactions, transfers, deposits "
        "and cash withdrawals are excluded.",
        "Periods use inclusive UTC dates. Transaction evidence is a bounded sample; "
        "aggregates use all qualifying records.",
        "Changes identify recorded contributors, not causes, savings or future bills.",
    ]
    equal_duration = baseline.duration_days == current.duration_days
    if not equal_duration:
        caveats.append(
            "Periods have unequal durations; total changes are not changes "
            "in a daily spending rate."
        )
    if "unknown_merchant" in merchants:
        caveats.append(
            "Missing merchant IDs are combined as unidentified merchants; "
            "matching names do not establish identity."
        )
    result = SpendingComparison(
        comparison_id=comparison_id,
        source=_label(bound.snapshot.source)[0],
        source_hash=source_hash,
        baseline=baseline,
        current=current,
        equal_duration=equal_duration,
        total=total,
        categories=category_section,
        merchants=merchant_section,
        findings=_findings(
            comparison_id, request, total, category_section, merchant_section, categories, merchants
        ),
        transaction_evidence=evidence,
        evidence_summary=_evidence_summary(evidence, eligible),
        caveats=tuple(caveats),
    )
    # Payload bounds never change aggregate arithmetic or silently cut identities.
    while len(result.model_dump_json().encode()) > MAX_COMPARISON_BYTES and evidence:
        evidence = evidence[:-1]
        result = result.model_copy(
            update={
                "transaction_evidence": evidence,
                "evidence_summary": _evidence_summary(evidence, eligible),
            }
        )
    if len(result.model_dump_json().encode()) > MAX_COMPARISON_BYTES:
        raise ValueError("Comparison exceeds the supported response size")
    return result


def validate_finding_assertion(
    trusted_comparison: SpendingComparison, proposal: FindingAssertion
) -> ComparisonFinding:
    """Resolve only an assertion about a server-generated comparison, never client prose."""
    proposal = FindingAssertion.model_validate(proposal)
    expected_windows = {
        name: {
            "start": getattr(trusted_comparison, name).start,
            "end": getattr(trusted_comparison, name).end,
        }
        for name in ("baseline", "current")
    }
    if (
        proposal.comparison_id != trusted_comparison.comparison_id
        or proposal.source_hash != trusted_comparison.source_hash
        or proposal.calculation_version != trusted_comparison.calculation_version
        or any(
            getattr(proposal, name).model_dump() != expected_windows[name]
            for name in expected_windows
        )
    ):
        raise ValueError("Finding identity does not match the comparison")
    finding = next(
        (row for row in trusted_comparison.findings if row.finding_id == proposal.finding_id), None
    )
    if finding is None or proposal.assertion != finding.assertion:
        raise ValueError("Unsupported finding or assertion")
    return finding
