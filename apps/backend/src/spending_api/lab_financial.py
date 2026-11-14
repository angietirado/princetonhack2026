"""Server-selected financial context and guards shared by every shopping interface.

Public selection is limited to fictional fixture profiles. There is deliberately no
public endpoint that accepts a bank key, customer ID, raw wallet, or arbitrary cash cap.
"""

import copy
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, StrictInt
from uuid import UUID

from spending_api.lab_engine import evaluate, fingerprint, recommend
from spending_api.lab_models import Constraints, InputModel
from spending_api.lab_store import LabNotFound
from spending_api.lab_wallet import (
    MAX_CENTS,
    ExpectedIncome,
    WalletDataError,
    WalletEvent,
    make_fixture_wallets,
    project_wallet,
    validate_wallet,
)

FIXTURE_NOTICE = (
    "These wallets are fictional local examples, not live Nessie captures or real people's "
    "finances. Checking cash and dated example bills constrain this simulation."
)
PREVIEW_NOTICE = (
    "This previews one current shopping trip against the frozen wallet. Saved revisions "
    "are alternatives, not extra purchases. No bank balance or frozen snapshot is changed."
)
REVIEW_NOTICE = (
    "The financial context needs review. An allowance cannot be established, so no "
    "affordability claim or shopping recommendation is available."
)
PROFILE_LABELS = {
    "steady": ("Steady week", "A $400 checking-cash week with a $180 example bill due on day 2."),
    "tight": ("Tight week", "A $140 checking-cash week with a $110 example bill due on day 2."),
    "buffer": ("Building a buffer", "A $600 checking-cash week with a $240 example bill due on day 2."),
}


class ProjectionInputs(InputModel):
    days: Annotated[StrictInt, Field(ge=1, le=31)] = 7
    reserve_cents: Annotated[StrictInt, Field(ge=0, le=MAX_CENTS)] = 0
    expected_income: list[ExpectedIncome] = Field(default_factory=list, max_length=100)
    events: list[WalletEvent] = Field(default_factory=list, max_length=100)


def create_financial_catalog() -> dict[str, dict]:
    """Deterministic fixture definitions. Only selection, never balances, comes from clients."""
    snapshots = make_fixture_wallets()
    catalog = {}
    for profile_id, snapshot in zip(PROFILE_LABELS, snapshots, strict=True):
        name, description = PROFILE_LABELS[profile_id]
        inputs = ProjectionInputs().model_dump(mode="json")
        catalog[profile_id] = {
            "profile_id": profile_id, "profile_name": name, "description": description,
            "snapshot": snapshot, "projection_inputs": inputs,
            "projection": project_wallet(snapshot, **inputs),
        }
    return catalog


def wallet_catalog_response(catalog: dict) -> dict:
    wallets = []
    for identity, context in catalog.items():
        summary = financial_summary({"financial_context": context})
        wallets.append({
            "id": identity, "name": context["profile_name"], "source": "fixture",
            "description": context["description"], "snapshot_id": context["snapshot"]["id"],
            "available_cents": summary["baseline_available_cents"],
            "scheduled_obligations_cents": summary["scheduled_obligations_cents"],
            "spendable_now_cents": summary["spendable_now_cents"],
            "shopping_allowance_cents": summary["shopping_allowance_cents"],
            "reserve_cents": summary["reserve_cents"],
        })
    return {"wallets": wallets, "notice": FIXTURE_NOTICE}


def attach_wallet(scenario: dict, profile_id: str | None, catalog: dict) -> dict:
    result = copy.deepcopy(scenario)
    if profile_id is not None:
        if profile_id not in catalog:
            raise LabNotFound("Wallet profile not found")
        result["financial_context"] = copy.deepcopy(catalog[profile_id])
    return result


def _context_parts(scenario: dict):
    context = scenario["financial_context"]
    if not isinstance(context, dict):
        raise WalletDataError("Financial context must be an object")
    snapshot = validate_wallet(context["snapshot"])
    inputs = ProjectionInputs.model_validate(context["projection_inputs"]).model_dump(mode="json")
    return context, snapshot, inputs


def _unavailable_summary():
    return {
        "status": "needs_review", "source": "unknown", "snapshot_id": None,
        "baseline_available_cents": None, "scheduled_obligations_cents": None,
        "shopping_allowance_cents": None, "spendable_now_cents": None,
        "reserve_cents": None, "shortfall_cents": None, "ending_available_cents": None,
        "allowed_travel_modes": [], "assumptions": [REVIEW_NOTICE],
        "unresolved": [{"code": "invalid_frozen_wallet", "message": REVIEW_NOTICE}],
    }


def financial_summary(scenario: dict) -> dict | None:
    """Return verified projection, None for old sessions, or a fail-closed review state.

    The cached context.projection is for display/storage only and is never trusted
    as an authoritative client-supplied budget.
    """
    if "financial_context" not in scenario:
        return None
    try:
        _context, snapshot, inputs = _context_parts(scenario)
        return project_wallet(snapshot, **inputs)
    except (WalletDataError, ValueError, KeyError, TypeError, AttributeError):
        return _unavailable_summary()


def _guard(scenario: dict, evaluation: dict, summary: dict) -> dict:
    result = copy.deepcopy(evaluation)
    violations = list(result["violations"])
    allowance = summary.get("spendable_now_cents")
    ready = (
        summary.get("status") == "ready"
        and type(allowance) is int and allowance >= 0
        and isinstance(summary.get("allowed_travel_modes"), list)
    )
    if not ready:
        violations.append(REVIEW_NOTICE)
    else:
        if result["total_cost_cents"] > allowance:
            violations.append(
                f"This trip exceeds the frozen wallet's ${allowance / 100:.2f} available for shopping now."
            )
        if result["constraints"]["travel_mode"] not in summary["allowed_travel_modes"]:
            violations.append("This travel mode is unavailable in the frozen wallet scenario.")
    result["violations"] = violations
    result["feasible"] = not violations
    after = None
    if ready:
        try:
            _context, snapshot, inputs = _context_parts(scenario)
            decisions = [] if result["total_cost_cents"] == 0 else [{
                "id": "current-shopping-choice", "kind": "shopping", "label": "Current shopping choice",
                "amount_cents": result["total_cost_cents"], "occurred_at": snapshot["captured_at"],
            }]
            after = project_wallet(snapshot, decisions=decisions, **inputs)
        except (WalletDataError, ValueError, KeyError, TypeError):
            result["violations"].append(REVIEW_NOTICE)
            result["feasible"] = False
    result["wallet"] = {"summary": summary, "after_choice": after, "notice": PREVIEW_NOTICE}
    return result


def financial_evaluate(scenario: dict, assignments: dict, constraints: dict) -> dict:
    result = evaluate(scenario, assignments, constraints)
    summary = financial_summary(scenario)
    return result if summary is None else _guard(scenario, result, summary)


def financial_recommend(scenario: dict, constraints: dict) -> dict:
    original = Constraints.model_validate(constraints).model_dump(mode="json")
    summary = financial_summary(scenario)
    if summary is None:
        return recommend(scenario, original)
    allowance = summary.get("spendable_now_cents")
    ready = summary.get("status") == "ready" and type(allowance) is int and allowance >= 0
    base = {
        "plans": [], "evaluated_plan_count": 0, "feasible_plan_count": 0,
        "wallet_summary": summary, "effective_spending_cap_cents": None,
    }
    if not ready:
        return {**base, "status": "needs_review", "notice": REVIEW_NOTICE}
    cap = min(original["budget_cents"], allowance)
    base["effective_spending_cap_cents"] = cap
    if original["travel_mode"] not in summary.get("allowed_travel_modes", []):
        return {
            **base, "status": "no_feasible_plan",
            "notice": (
                "No plan fits the wallet's available cash and travel modes. Your stated money "
                "and time limits were not changed."
            ),
        }
    # Add a distinct financial bound to the optimizer, preserving the ORIGINAL user constraints.
    # This preserves a distinct financial boundary without silently rewriting their budget.
    result = recommend(scenario, original, cost_cap_cents=cap)
    plans = []
    for plan in result["plans"]:
        checked = _guard(scenario, evaluate(scenario, plan["assignments"], original), summary)
        if checked["feasible"]:
            plans.append({
                **checked, "label": plan["label"],
                "id": fingerprint({
                    "assignments": checked["assignments"], "constraints": original,
                    "wallet_snapshot_id": summary["snapshot_id"],
                    "wallet_projection_id": summary.get("id"),
                })[:16],
            })
    return {
        **result, "plans": plans, "status": "ok" if plans else "no_feasible_plan",
        "wallet_summary": summary, "effective_spending_cap_cents": cap,
        "notice": (
            result["notice"] + f" The separate wallet cap is ${allowance / 100:.2f} available now. "
            "Your stated constraints are preserved."
        ),
    }


def register_financial_routes(app, store, catalog):
    bearer = HTTPBearer(auto_error=False)

    @app.get("/api/v1/wallets")
    def wallet_catalog():
        return wallet_catalog_response(catalog)

    @app.get("/api/v1/sessions/{session_id}/wallet")
    def session_wallet(
        session_id: UUID,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        session = store.session(
            str(session_id), credentials.credentials if credentials else None
        )
        context = session["scenario"].get("financial_context")
        profile = {
            "id": context.get("profile_id"), "name": context.get("profile_name")
        } if isinstance(context, dict) else None
        summary = financial_summary(session["scenario"])
        notice = (
            FIXTURE_NOTICE if summary and summary.get("source") == "fixture" else
            "This session has no wallet attached." if summary is None else
            "This is the session's frozen financial context; no bank balance is changed."
        )
        return {"profile": profile, "summary": summary, "notice": notice}
