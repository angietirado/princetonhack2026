"""Bounded, deterministic audits of recorded initial shopping choices.

Analyst probes describe modeled alternatives. They do not infer motives, change
choices, pool financially different scenarios, or estimate causal behavior.
"""

import json
import math
from typing import Annotated

from fastapi import Depends, Query

from spending_api.lab_engine import fingerprint
from spending_api.lab_financial import financial_evaluate, financial_summary
from spending_api.lab_investigator import investigate_decision
from spending_api.lab_models import Constraints, DataMode, Scenario
from spending_api.lab_store import LabStore

MAX_SESSIONS = 200
COUNT_NAMES = (
    "initial_choices", "selected_plan_fits", "other_plan_fits", "no_plan_fits",
    "needs_review", "newly_feasible_under_probe", "modeled_dominated",
)
ALL_LIMITS = ("budget_cents", "time_budget_minutes", "travel_mode", "max_stores")


def _selected_rows(store, scenario_id, data_mode):
    clauses, values = [], []
    if scenario_id is not None:
        clauses.append("scenario_id=?")
        values.append(scenario_id)
    if data_mode != "all":
        clauses.append("is_demo=?")
        values.append(int(data_mode == "demo"))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with store.connect() as connection:
        # Count, selection, and initial-choice lookup share one read snapshot.
        connection.execute("BEGIN")
        total = connection.execute("SELECT COUNT(*) FROM sessions" + where, values).fetchone()[0]
        rows = connection.execute(
            "SELECT id,scenario_snapshot,scenario_hash,is_demo FROM sessions" + where + " ORDER BY created_at DESC,id LIMIT ?",
            [*values, MAX_SESSIONS],
        ).fetchall()
        records = []
        for row in rows:
            initial = connection.execute(
                "SELECT evaluation FROM choices WHERE session_id=? AND stage='initial' ORDER BY sequence LIMIT 1",
                (row["id"],),
            ).fetchone()
            records.append((dict(row), initial["evaluation"] if initial else None))
    return total, records


def _investigate(scenario, constraints, wallet):
    # All limits are protected: this audit asks whether a plan exists at these
    # exact settings, not for free-form relaxations or inferred mode access.
    return investigate_decision(
        scenario, constraints, protected_constraints=ALL_LIMITS,
        allowed_travel_modes=[constraints["travel_mode"]], wallet_summary=wallet,
    )


def _dominance(scenario, constraints, selected, wallet):
    """An exact matched-cost/time query, independent of sampled frontier points."""
    matched = {
        **constraints,
        "budget_cents": min(constraints["budget_cents"], max(1, selected["total_cost_cents"])),
        "time_budget_minutes": min(
            constraints["time_budget_minutes"],
            max(math.nextafter(0.0, 1.0), selected["total_minutes"]),
        ),
    }
    result = _investigate(scenario, matched, wallet)
    return any(
        plan["total_cost_cents"] <= selected["total_cost_cents"]
        and plan["total_minutes"] <= selected["total_minutes"]
        and (plan["total_cost_cents"] < selected["total_cost_cents"]
             or plan["total_minutes"] < selected["total_minutes"])
        for plan in result["feasible_plans"]
    )


def audit_choices(
    store: LabStore, *, scenario_id: str | None = None, data_mode: DataMode = "participants",
    budget_delta_cents: int = 0, time_delta_minutes: int = 0,
) -> dict:
    """Audit at most 200 latest filtered sessions, always using their initial choice.

    The four feasibility categories partition valid initial choices. Dominance
    is evaluated only for feasible selected plans. Probe unlocks are counted only
    among baseline no-plan-fits cases, excluding unknown wallet availability.
    """
    if data_mode not in {"participants", "demo", "all"}:
        raise ValueError("Unknown data mode")
    if type(budget_delta_cents) is not int or not 0 <= budget_delta_cents <= 10000:
        raise ValueError("Budget delta must be an integer from 0 through 10000 cents")
    if type(time_delta_minutes) is not int or not 0 <= time_delta_minutes <= 120:
        raise ValueError("Time delta must be an integer from 0 through 120 minutes")
    if scenario_id is not None and (not isinstance(scenario_id, str) or len(scenario_id) > 80):
        raise ValueError("Scenario filter is invalid")
    scenario_id = scenario_id or None
    total, rows = _selected_rows(store, scenario_id, data_mode)
    cohorts = {}
    no_initial = invalid_initial = analyzed = 0
    for row, raw_initial in rows:
        if raw_initial is None:
            no_initial += 1
            continue
        try:
            scenario = Scenario.model_validate_json(row["scenario_snapshot"]).model_dump(mode="json")
            initial = json.loads(raw_initial)
            constraints = Constraints.model_validate(initial["constraints"]).model_dump(mode="json")
            # Never trust cached arithmetic, feasibility flags, or revised choices.
            selected = financial_evaluate(scenario, initial["assignments"], constraints)
            wallet = financial_summary(scenario)
            baseline = _investigate(scenario, constraints, wallet)
        except (ValueError, KeyError, TypeError):
            invalid_initial += 1
            continue
        analyzed += 1
        cohort_id = "cohort_" + fingerprint({
            "scenario_hash": row["scenario_hash"], "constraints": constraints, "is_demo": bool(row["is_demo"]),
        })[:20]
        probe_constraints = {
            **constraints,
            "budget_cents": min(1_000_000, constraints["budget_cents"] + budget_delta_cents),
            "time_budget_minutes": min(1440, constraints["time_budget_minutes"] + time_delta_minutes),
        }
        if cohort_id not in cohorts:
            cohorts[cohort_id] = {
                "id": cohort_id, "scenario_id": scenario["id"], "scenario_name": scenario["name"],
                "scenario_version": scenario["version"], "scenario_hash": row["scenario_hash"],
                "world_version": scenario.get("world_version") if isinstance(scenario.get("world_version"), str) else None,
                "is_demo": bool(row["is_demo"]), "initial_constraints": constraints,
                "wallet_attached": wallet is not None,
                "wallet_source": wallet.get("source", "unknown") if wallet else None,
                "financial_context": ({
                    "profile_name": (
                        scenario["financial_context"].get("profile_name", "Frozen wallet")[:200]
                        if isinstance(scenario.get("financial_context"), dict)
                        and isinstance(scenario["financial_context"].get("profile_name", "Frozen wallet"), str)
                        else "Frozen wallet"
                    ),
                    "source": wallet.get("source", "unknown"), "status": wallet.get("status"),
                    "spendable_now_cents": wallet.get("spendable_now_cents"),
                } if wallet is not None else None),
                "probe_constraints": probe_constraints,
                "probe_clipped_by_app_bounds": (
                    probe_constraints["budget_cents"] != constraints["budget_cents"] + budget_delta_cents
                    or probe_constraints["time_budget_minutes"] != constraints["time_budget_minutes"] + time_delta_minutes
                ),
                "counts": {name: 0 for name in COUNT_NAMES},
                "denominators": {
                    "classified_initial_choices": 0, "probe_eligible_initial_choices": 0,
                    "baseline_no_feasible_initial_choices": 0, "dominance_eligible_initial_choices": 0,
                },
                "example_ids": {name: [] for name in COUNT_NAMES if name != "initial_choices"},
            }
        cohort = cohorts[cohort_id]
        counts, denominators = cohort["counts"], cohort["denominators"]

        def count(name):
            counts[name] += 1
            if name in cohort["example_ids"] and len(cohort["example_ids"][name]) < 3:
                cohort["example_ids"][name].append(row["id"])

        count("initial_choices")
        denominators["classified_initial_choices"] += 1
        if baseline["status"] == "needs_review":
            count("needs_review")
            continue
        denominators["probe_eligible_initial_choices"] += 1
        if selected["feasible"]:
            count("selected_plan_fits")
            denominators["dominance_eligible_initial_choices"] += 1
            if _dominance(scenario, constraints, selected, wallet):
                count("modeled_dominated")
        elif baseline["status"] == "feasible":
            count("other_plan_fits")
        else:
            count("no_plan_fits")
            denominators["baseline_no_feasible_initial_choices"] += 1
            if probe_constraints != constraints and _investigate(scenario, probe_constraints, wallet)["status"] == "feasible":
                count("newly_feasible_under_probe")

    return {
        "data_mode": data_mode, "scenario_id": scenario_id,
        "probe": {"budget_delta_cents": budget_delta_cents, "time_delta_minutes": time_delta_minutes,
                  "combined": bool(budget_delta_cents and time_delta_minutes)},
        "total_sessions": total, "selected_sessions": len(rows), "analyzed_sessions": analyzed,
        "skipped_no_initial_sessions": no_initial, "skipped_invalid_initial_sessions": invalid_initial,
        "truncated": total > MAX_SESSIONS, "max_sessions": MAX_SESSIONS,
        "cohorts": [cohorts[key] for key in sorted(cohorts)],
        "notices": [
            "The selection is the most recent 200 filtered sessions, ordered by created_at descending then session ID ascending. It is not a random sample.",
            "Only each selected session's earliest initial choice is analyzed; revised or repeated choices do not add observations.",
            "Selected sessions include those without an initial choice. Analyzed sessions are valid initial choices; skipped counts are reported separately.",
            "Cohorts keep exact frozen scenario hashes, initial constraints, and participant/demo status separate, including different wallet contexts and source versions.",
            "The four feasibility counts partition valid initial choices. Needs-review wallets are excluded from probe and dominance denominators.",
            "Newly feasible counts use baseline no-plan-fits initial choices as the denominator; budget and time increases apply together when both are nonzero.",
            "Probes preserve basket quantities, available wallet cash, travel mode, and store limit. App bounds cap the displayed probe constraints; no saved record is changed.",
            "Modeled dominance means an alternative feasible plan costs no more and takes no longer, improving at least one. Its denominator is feasible selected plans only.",
            "Prices and travel are modeled; unrecorded preferences may matter. These results do not explain motives, measure real purchases, or estimate a causal effect.",
        ],
    }


def register_audit_routes(app, store: LabStore, require_admin):
    """Expose only fixed filters and probes under the existing analyst dependency."""
    @app.get("/api/v1/analytics/audit", dependencies=[Depends(require_admin)])
    def audit(
        scenario_id: Annotated[str | None, Query(max_length=80)] = None,
        data_mode: DataMode = "participants",
        budget_delta_cents: Annotated[int, Query(ge=0, le=10000)] = 0,
        time_delta_minutes: Annotated[int, Query(ge=0, le=120)] = 0,
    ):
        return audit_choices(
            store, scenario_id=scenario_id, data_mode=data_mode,
            budget_delta_cents=budget_delta_cents, time_delta_minutes=time_delta_minutes,
        )
