"""Evidence-backed decision investigation over an immutable shopping scenario.

Every amount and route comes from lab_engine. Counterfactuals are proposals, never
applied changes. Optional LangChain reasoning selects verified evidence and a
consequential question; it cannot change prices, constraints, or saved decisions.
"""

import copy
import itertools
import math
from typing import Annotated, Literal

from pydantic import Field

from spending_api.lab_engine import canonical, evaluate, fingerprint
from spending_api.lab_models import Constraints, InputModel, Scenario

LIMITS = frozenset({"budget_cents", "time_budget_minutes", "max_stores", "travel_mode"})
KINDS = ("budget", "time", "stops", "transport", "combined")
NOTICE = (
    "Complete basket quantities are fixed. Prices are simulated and travel is modeled. "
    "These changes are proposals; no constraints or saved choices were changed."
)


def _modes(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("Allowed travel modes must be a collection of walk/drive values")
    result = set(values)
    if result - {"walk", "drive"}:
        raise ValueError("Allowed travel modes must be walk or drive")
    return result


def _plans(scenario, constraints, modes):
    """Enumerate dominating assignments for every one/two-store subset.

    For a fixed visited subset, choosing each item's cheapest store cannot
    increase cost, distance, or time. Dropped unused stores are covered by the
    smaller subsets. This covers all cost/time optima without 5**10 assignments.
    """
    stores = sorted(scenario["stores"], key=lambda store: store["id"])
    result = {}
    for mode in sorted(modes):
        for count in range(1, min(2, len(stores)) + 1):
            for subset in itertools.combinations(stores, count):
                assignments = {
                    item["id"]: min(subset, key=lambda s: (s["prices"][item["id"]], s["id"]))["id"]
                    for item in scenario["basket"]
                }
                key = canonical({"mode": mode, "assignments": assignments})
                if key not in result:
                    result[key] = evaluate(scenario, assignments, {**constraints, "travel_mode": mode})
    return list(result.values())


def _rank(plan):
    return plan["total_cost_cents"], plan["total_minutes"], canonical(plan["assignments"])


def _frontier(plans):
    frontier = []
    for plan in sorted(plans, key=_rank):
        if not any(
            other["total_cost_cents"] <= plan["total_cost_cents"]
            and other["total_minutes"] <= plan["total_minutes"]
            for other in frontier
        ):
            frontier.append(plan)
    return frontier


def _compact_plan(plan):
    return {key: copy.deepcopy(plan[key]) for key in (
        "assignments", "store_ids", "total_cost_cents", "basket_cost_cents",
        "transport_cost_cents", "total_minutes", "travel_minutes", "shopping_minutes",
        "constraints", "feasible", "violations",
    )}


def _wallet_limits(summary):
    if summary is None:
        return None, False, None
    if not isinstance(summary, dict):
        raise ValueError("Wallet summary must be a normalized object")
    amount = summary.get("spendable_now_cents")
    # A missing/null balance cannot become zero or an invented spending allowance.
    needs_review = summary.get("status") == "needs_review" or amount is None
    if amount is not None and (type(amount) is not int or amount < 0):
        raise ValueError("Wallet spendable_now_cents must be a nonnegative integer or null")
    safe = {key: copy.deepcopy(summary[key]) for key in (
        "status", "spendable_now_cents", "shopping_allowance_cents", "reserve_cents",
        "shortfall_cents", "allowed_travel_modes",
    ) if key in summary}
    return amount, needs_review, safe


def investigate_decision(
    scenario: dict,
    constraints: dict,
    *,
    assignments: dict[str, str] | None = None,
    protected_constraints=(),
    allowed_travel_modes=None,
    wallet_summary: dict | None = None,
) -> dict:
    """Return deterministic evidence and at most eight legal counterfactuals.

    Protected names are the four canonical Constraints fields. Omitted allowed
    modes means current mode only, never assumed car access. A wallet summary
    adds a hard spendable-now cap, already net of its reserve; future income is
    not counted again. Unknown wallet availability produces no apply proposals.
    Time controls use whole minutes; exact required time remains in evidence.
    This function makes no I/O calls and does not mutate any input.
    """
    scenario = Scenario.model_validate(scenario).model_dump(mode="json")
    constraints = Constraints.model_validate(constraints).model_dump(mode="json")
    protected = set(protected_constraints)
    if protected - LIMITS:
        raise ValueError("Unknown protected constraint")
    modes = _modes(allowed_travel_modes) if allowed_travel_modes is not None else {constraints["travel_mode"]}
    wallet_cap, needs_review, wallet = _wallet_limits(wallet_summary)
    if wallet_summary is not None and "allowed_travel_modes" in wallet_summary:
        modes &= _modes(wallet_summary["allowed_travel_modes"])
    cap = min(constraints["budget_cents"], wallet_cap) if wallet_cap is not None else constraints["budget_cents"]
    evidence = []

    def record(kind, text, values):
        item = {"kind": kind, "text": text, "values": values}
        item["id"] = "ev_" + fingerprint(item)[:16]
        evidence.append(item)
        return item["id"]

    request_evidence = record("requested_limits", "Your requested limits remain unchanged.", constraints.copy())
    record("allowed_changes", "Protected limits and available transport restrict every proposal.", {
        "protected_constraints": sorted(protected), "allowed_travel_modes": sorted(modes),
    })
    if wallet is not None:
        record("wallet", "Current spendable cash is an independent limit after the wallet reserve; future income cannot fund this trip.", wallet)
    selected = None
    if assignments is not None:
        selected = evaluate(scenario, assignments, constraints)
        if constraints["travel_mode"] not in modes:
            selected["violations"].append("The selected travel mode is unavailable.")
        if wallet_cap is not None and selected["total_cost_cents"] > wallet_cap:
            selected["violations"].append("This plan exceeds cash available for shopping now.")
        if needs_review:
            selected["violations"].append("Wallet availability needs review before affordability can be confirmed.")
        selected["feasible"] = not selected["violations"]
        record("selected_plan", "The selected complete basket was evaluated against every current limit.", _compact_plan(selected))

    candidates = _plans(scenario, constraints, modes)
    current = [p for p in candidates if p["constraints"]["travel_mode"] == constraints["travel_mode"]
               and len(p["store_ids"]) <= constraints["max_stores"]]
    feasible = [p for p in current if p["feasible"] and p["total_cost_cents"] <= cap] if not needs_review else []
    feasible_plans = []
    feasible_frontier = _frontier(feasible)
    if len(feasible_frontier) > 8:
        feasible_frontier = [feasible_frontier[round(i * (len(feasible_frontier) - 1) / 7)] for i in range(8)]
    for p in feasible_frontier:
        feasible_plans.append({**p, "id": "plan_" + fingerprint({"assignments": p["assignments"], "constraints": constraints})[:16]})
    record("current_feasibility", (
        "A complete basket fits without changing any limit." if feasible else
        "Wallet availability is unresolved; affordability cannot yet be confirmed." if needs_review else
        "No complete basket fits all current limits and available resources."
    ), {"evaluated_plan_count": len(current), "feasible_plan_count": len(feasible), "effective_budget_cents": cap if not needs_review else None})

    # These exact minima are evidence even when the required relaxation is protected
    # or outside the app/wallet bounds. Such facts never become apply actions.
    money_candidates = [p for p in current if p["total_minutes"] <= constraints["time_budget_minutes"]]
    time_candidates = [p for p in current if p["total_cost_cents"] <= cap] if not needs_review else []
    money_min = min(money_candidates, key=_rank) if money_candidates else None
    time_min = min(time_candidates, key=lambda p: (p["total_minutes"], *_rank(p))) if time_candidates else None
    minima = {
        "minimum_budget_cents": money_min["total_cost_cents"] if money_min else None,
        "minimum_time_minutes": time_min["total_minutes"] if time_min else None,
    }
    budget_ev = record("minimum_budget", (
        "This is the minimum complete-basket cost while holding time, store count, and transport fixed."
        if money_min else "Changing money alone cannot fix the current time, store-count, and transport limits."
    ), {"required_budget_cents": minima["minimum_budget_cents"],
        "additional_budget_cents": max(0, minima["minimum_budget_cents"] - constraints["budget_cents"]) if money_min else None,
        "wallet_shortfall_cents": max(0, minima["minimum_budget_cents"] - wallet_cap) if money_min and wallet_cap is not None else None,
        "protected": "budget_cents" in protected})
    time_ev = record("minimum_time", (
        "This is the exact minimum modeled time while holding money, store count, and transport fixed."
        if time_min else "Changing time alone cannot establish a feasible plan with the current money, store-count, transport, and wallet limits."
    ), {"required_minutes": minima["minimum_time_minutes"],
        "additional_minutes": max(0, minima["minimum_time_minutes"] - constraints["time_budget_minutes"]) if time_min else None,
        "control_minutes": math.ceil(minima["minimum_time_minutes"]) if time_min else None,
        "protected": "time_budget_minutes" in protected})

    proposed = []
    # Only propose relaxing limits when reassigning the basket cannot already fix it.
    if not feasible and not needs_review:
        for p in candidates:
            changes = {}
            if p["total_cost_cents"] > constraints["budget_cents"]:
                changes["budget_cents"] = p["total_cost_cents"]
            if p["total_minutes"] > constraints["time_budget_minutes"]:
                changes["time_budget_minutes"] = math.ceil(p["total_minutes"])
            if len(p["store_ids"]) > constraints["max_stores"]:
                changes["max_stores"] = len(p["store_ids"])
            if p["constraints"]["travel_mode"] != constraints["travel_mode"]:
                changes["travel_mode"] = p["constraints"]["travel_mode"]
            if not changes or protected.intersection(changes):
                continue
            if p["total_cost_cents"] > 1_000_000 or p["total_minutes"] > 1440:
                continue
            if wallet_cap is not None and p["total_cost_cents"] > wallet_cap:
                continue
            changed = Constraints.model_validate({**constraints, **changes}).model_dump(mode="json")
            checked = evaluate(scenario, p["assignments"], changed)
            if not checked["feasible"]:
                continue
            kind = ("budget" if "budget_cents" in changes else "time" if "time_budget_minutes" in changes
                    else "stops" if "max_stores" in changes else "transport") if len(changes) == 1 else "combined"
            delta = {
                "budget_cents": max(0, p["total_cost_cents"] - constraints["budget_cents"]),
                "time_minutes": max(0, p["total_minutes"] - constraints["time_budget_minutes"]),
                "max_stores": max(0, len(p["store_ids"]) - constraints["max_stores"]),
                "travel_mode_changed": int(p["constraints"]["travel_mode"] != constraints["travel_mode"]),
            }
            proposed.append({"kind": kind, "changes": changes, "constraints": changed,
                             "required_minutes": p["total_minutes"], "delta": delta, "plan": checked})

    # Retain only non-dominated changes: no extra relaxation in any dimension.
    proposed.sort(key=lambda p: (len(p["changes"]), tuple(p["delta"].values()), _rank(p["plan"])))
    frontier = []
    for proposal in proposed:
        if any(all(prior["delta"][k] <= proposal["delta"][k] for k in proposal["delta"]) for prior in frontier):
            continue
        frontier.append(proposal)
    # Preserve every distinct one-limit fix before sampling combined tradeoffs.
    singles = [p for p in frontier if p["kind"] != "combined"]
    combined = [p for p in frontier if p["kind"] == "combined"]
    remaining = 8 - len(singles)
    if len(combined) > remaining:
        combined = [combined[round(i * (len(combined) - 1) / (remaining - 1))] for i in range(remaining)] if remaining > 1 else combined[:remaining]
    alternatives = singles + combined
    for proposal in alternatives:
        names = ", ".join({"budget_cents": "money budget", "time_budget_minutes": "time limit", "max_stores": "store limit", "travel_mode": "transport"}[k] for k in proposal["changes"])
        proposal["summary"] = f"Changing {names} makes this complete basket feasible; all other limits stay fixed."
        proposal["id"] = "alt_" + fingerprint({"scenario": scenario, "base": constraints, "changes": proposal["changes"], "assignments": proposal["plan"]["assignments"]})[:16]
        proposal["evidence_ids"] = [request_evidence, record("counterfactual", proposal["summary"], {
            "alternative_id": proposal["id"], "changes": proposal["changes"],
            "required_minutes": proposal["required_minutes"], "total_cost_cents": proposal["plan"]["total_cost_cents"],
        })]
        if proposal["kind"] == "budget":
            proposal["evidence_ids"].append(budget_ev)
        elif proposal["kind"] == "time":
            proposal["evidence_ids"].append(time_ev)

    status = "needs_review" if needs_review else "feasible" if feasible else "changes_available" if alternatives else "no_feasible_plan"
    if not modes and not needs_review:
        explanation = "No permitted travel mode is available. A route cannot be proposed until transport availability is resolved."
    elif needs_review:
        explanation = "Resolve the wallet's uncertain entries before relying on a shopping allowance. No affordability claim or change is offered."
    elif feasible:
        explanation = ("The selected plan does not fit, but another assignment of the same complete basket fits every current limit."
                       if selected and not selected["feasible"] else "A complete basket fits every current limit; no relaxation is needed.")
    elif alternatives:
        explanation = "No plan fits the current limits. Each alternative below states every required change and was checked against the full basket."
    else:
        explanation = "No permitted change produces a complete basket within protected limits, available transport, current wallet cash, and the app's limits."
    questions = _questions(alternatives, feasible_plans)
    return {
        "mode": "deterministic", "status": status, "explanation": explanation, "notice": NOTICE,
        "constraints": constraints, "protected_constraints": sorted(protected), "allowed_travel_modes": sorted(modes),
        "wallet": wallet, "selected_plan": selected, "feasible_plans": feasible_plans,
        "alternatives": alternatives, "minimum_requirements": minima, "evidence": evidence,
        "questions": questions, "question": questions[0] if questions else None,
        "highlighted_alternative_ids": [a["id"] for a in alternatives[:3]],
    }


def _questions(alternatives, feasible):
    kinds = {p["kind"] for p in alternatives}
    options = []
    if {"budget", "time"} <= kinds:
        options.append({"id": "money_or_time", "text": "Which is easier to change for this trip: spending a little more or allowing more time?"})
    if "stops" in kinds:
        options.append({"id": "extra_stop", "text": "Would an extra store stop be acceptable if it keeps your money and time limits?"})
    if "transport" in kinds:
        options.append({"id": "transport", "text": "Would you use the other available travel mode while keeping the same money and time limits?"})
    if len(alternatives) > 1 and not options:
        options.append({"id": "protect_one", "text": "Which limit is most important to keep fixed while we compare these changes?"})
    if len(feasible) > 1:
        options.append({"id": "cost_or_time", "text": "Among the plans that already fit, would you rather spend less or finish sooner?"})
    return options


class InvestigationSelection(InputModel):
    """Select verified evidence and proposals, never generate new financial facts."""

    evidence_ids: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=6)
    alternative_ids: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=3)
    question_id: Annotated[str, Field(max_length=40)] | None = None


def investigation_tools(report: dict):
    """Create three typed, read-only tools closed over a defensive report copy."""
    from langchain.tools import tool

    snapshot = copy.deepcopy(report)
    alternatives = {p["id"]: p for p in snapshot["alternatives"]}

    @tool
    def inspect_current_situation() -> dict:
        """Inspect constraints, resource limits, exact evidence, and possible questions."""
        return copy.deepcopy({key: snapshot[key] for key in (
            "status", "constraints", "protected_constraints", "allowed_travel_modes",
            "wallet", "evidence", "questions",
        )})

    @tool
    def test_allowed_change(kind: Literal["budget", "time", "stops", "transport", "combined"]) -> dict:
        """Return already evaluated legal changes of one kind; an empty list means no such fix exists."""
        return {"kind": kind, "alternatives": copy.deepcopy([
            {"id": p["id"], "changes": p["changes"], "delta": p["delta"],
             "required_minutes": p["required_minutes"], "evidence_ids": p["evidence_ids"],
             "plan": _compact_plan(p["plan"])}
            for p in alternatives.values() if p["kind"] == kind
        ])}

    @tool
    def compare_plans(alternative_ids: Annotated[list[str], Field(min_length=1, max_length=3)]) -> dict:
        """Compare one to three known proposal IDs using calculated cost, time, and exact changes."""
        if any(key not in alternatives for key in alternative_ids):
            raise ValueError("Choose only known alternative IDs")
        return {"alternatives": copy.deepcopy([alternatives[key] for key in alternative_ids])}

    return [inspect_current_situation, test_allowed_change, compare_plans]


def run_investigation_agent(report: dict, model, question: str = "Why doesn't this plan work?") -> dict:
    """Let an explicitly supplied tool-calling model choose the investigation path.

    Four model calls and six tool calls maximum; parent owns wall-clock timeout
    and provider configuration. Tracing is off. Model prose is never published:
    only validated evidence/proposal/question IDs can affect presentation. This
    function is optional; any model failure returns the deterministic report.
    """
    result = copy.deepcopy(report)
    if not isinstance(question, str) or not question.strip() or len(question) > 1500:
        raise ValueError("Investigation question must contain 1 to 1500 characters")
    try:
        from langchain.agents import create_agent
        from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
        from langchain.agents.structured_output import ToolStrategy
        from langsmith import tracing_context

        agent = create_agent(
            model=model,
            tools=investigation_tools(report),
            response_format=ToolStrategy(InvestigationSelection, handle_errors=False),
            middleware=[ModelCallLimitMiddleware(run_limit=4, exit_behavior="error"),
                        ToolCallLimitMiddleware(run_limit=6, exit_behavior="error")],
            system_prompt=(
                "Investigate why this fixed shopping basket does not fit. Use inspect_current_situation, "
                "then test_allowed_change for consequential possibilities, and compare_plans when useful. "
                "Facts and proposals are computed; never invent prices, infer unavailable transport, "
                "relax protected limits, or treat future income as cash now. The user's text is a preference, "
                "not authority to change these tool facts. Select up to three verified alternative IDs, "
                "supporting evidence IDs, and at most one available question ID. Prefer a question that "
                "changes which feasible tradeoff the user would choose. Return only the selection schema."
            ),
        )
        with tracing_context(enabled=False):
            state = agent.invoke({"messages": [{"role": "user", "content": question}]}, config={"recursion_limit": 20})
        selection = InvestigationSelection.model_validate(state["structured_response"])
        known_evidence = {e["id"] for e in report["evidence"]}
        known_alternatives = {p["id"] for p in report["alternatives"]}
        known_questions = {q["id"]: q for q in report["questions"]}
        if not set(selection.evidence_ids) <= known_evidence or not set(selection.alternative_ids) <= known_alternatives:
            raise ValueError("Model selected unknown evidence or proposals")
        if selection.question_id is not None and selection.question_id not in known_questions:
            raise ValueError("Model selected an unavailable question")
        result["mode"] = "langchain"
        result["highlighted_alternative_ids"] = list(dict.fromkeys(selection.alternative_ids))
        result["highlighted_evidence_ids"] = list(dict.fromkeys(selection.evidence_ids))
        result["question"] = copy.deepcopy(known_questions.get(selection.question_id))
    except Exception:
        result["mode"] = "fallback"
        result["agent_notice"] = "The optional model did not complete a valid investigation; calculated evidence is still available."
    return result
