"""Controlled home-location comparisons over one immutable shopping world.

Only home changes. This is a read-only simulation, not a moving recommendation,
monthly affordability calculation, or inference about anyone's income.
"""

import copy
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from spending_api.lab_engine import canonical, fingerprint
from spending_api.lab_financial import financial_evaluate, financial_recommend, financial_summary
from spending_api.lab_models import Constraints, Identifier, InputModel, Scenario
from spending_api.lab_store import LabConflict, LabStore

MAX_OTHER_HOMES = 3
EVALUATION_FIELDS = (
    "basket_cost_cents", "transport_cost_cents", "total_cost_cents", "travel_minutes",
    "shopping_minutes", "total_minutes", "distance_km", "feasible", "violations",
    "store_ids", "route", "assignments", "constraints",
)


class LocationComparisonRequest(InputModel):
    constraints: Constraints | None = None
    assignments: dict[Identifier, Identifier] | None = Field(default=None, min_length=1, max_length=10)


def _world(scenario):
    return {key: scenario[key] for key in ("basket", "stores", "travel")}


def _text(value):
    return " ".join(value.casefold().split()) if isinstance(value, str) else ""


def _same_home(left, right):
    """Prefer physical property/unit identity over potentially refreshed listings."""
    if canonical(left) == canonical(right):
        return True
    left_unit, right_unit = _text(left.get("unit_number")), _text(right.get("unit_number"))
    known_different_units = bool(left_unit and right_unit and left_unit != right_unit)
    same_source = _text(left.get("source")) == _text(right.get("source"))
    if same_source:
        for field in ("source_property_id", "source_record_id", "source_residential_listing_id"):
            a, b = left.get(field), right.get(field)
            if a is not None and a != "" and b is not None and str(a) == str(b):
                return not known_different_units
    same_coordinates = left["latitude"] == right["latitude"] and left["longitude"] == right["longitude"]
    if same_coordinates and not known_different_units:
        a, b = _text(left.get("recorded_address")), _text(right.get("recorded_address"))
        if not a or not b or a == b:
            return True
    return False


def _rent(home):
    amount = home.get("monthly_rent_cents")
    # CoStar monetary unit 1 is USD in this supplied sample. Do not interpret a
    # sale price, unidentified currency, or an unlabeled housing number as rent.
    if (home.get("housing_price_basis") == "monthly_rent"
            and type(home.get("source_monetary_unit")) is int and home["source_monetary_unit"] == 1
            and type(amount) is int and 0 <= amount <= 1_000_000_000):
        return amount
    return None


def _public_evaluation(value):
    return {key: copy.deepcopy(value[key]) for key in EVALUATION_FIELDS}


def _wallet_summary(value):
    if value is None:
        return None
    return {key: copy.deepcopy(value.get(key)) for key in (
        "source", "status", "spendable_now_cents", "reserve_cents", "shopping_allowance_cents",
        "shortfall_cents", "allowed_travel_modes",
    )}


def compare_locations(
    scenario: dict, constraints: dict, assignments: dict[str, str], candidate_scenarios: dict | list,
) -> dict:
    """Compare baseline and up to three distinct compatible homes, with no I/O.

    Declared world IDs/versions and actual basket/stores/travel must all match.
    Candidate wallets, defaults, and other metadata never enter calculation.
    The current scenario identity and frozen physical home are never replaced
    by a newer catalog record. Return values are defensive copies.
    """
    scenario = Scenario.model_validate(scenario).model_dump(mode="json")
    constraints = Constraints.model_validate(constraints).model_dump(mode="json")
    if not isinstance(assignments, dict):
        raise ValueError("Assign every basket item to a known store")
    assignments = copy.deepcopy(assignments)
    if not isinstance(candidate_scenarios, (dict, list)):
        raise ValueError("Candidate catalog must be a scenario mapping or list")
    catalog = list(candidate_scenarios.values()) if isinstance(candidate_scenarios, dict) else list(candidate_scenarios)
    if len(catalog) > 100:
        raise ValueError("Candidate catalog exceeds 100 scenarios")
    original_evaluation = financial_evaluate(scenario, assignments, constraints)
    wallet = financial_summary(scenario)
    world_content = _world(scenario)
    world_id, world_version = scenario.get("world_id"), scenario.get("world_version")
    supported = all(isinstance(value, str) and value.strip() for value in (world_id, world_version))
    baseline_rent = _rent(scenario["home"])
    baseline_hash = fingerprint(scenario)

    def entry(source, current=False):
        changed = copy.deepcopy(scenario)
        changed["home"] = copy.deepcopy(source["home"])
        comparison_hash = fingerprint(changed)
        evaluated = financial_evaluate(changed, assignments, constraints)
        optimized = financial_recommend(changed, constraints)
        plans = []
        for plan in optimized["plans"]:
            plans.append({
                **_public_evaluation(plan), "label": plan["label"],
                "id": "location_plan_" + fingerprint({
                    "scenario_hash": comparison_hash, "constraints": constraints, "assignments": plan["assignments"],
                })[:20],
            })
        rent = _rent(source["home"])
        return {
            "id": "current" if current else "home_" + fingerprint(source["home"])[:20],
            "scenario_id": source["id"], "scenario_version": source["version"],
            "home_hash": fingerprint(source["home"]), "comparison_scenario_hash": comparison_hash,
            "is_current": current, "home": copy.deepcopy(source["home"]),
            "same_assignment": {
                "evaluation": _public_evaluation(evaluated),
                "delta": {key: evaluated[key] - original_evaluation[key] for key in (
                    "basket_cost_cents", "transport_cost_cents", "total_cost_cents", "total_minutes",
                )},
            },
            "optimized": {"status": optimized["status"], "plans": plans,
                          "feasible_plan_count": optimized["feasible_plan_count"],
                          "evaluated_plan_count": optimized["evaluated_plan_count"], "notice": optimized["notice"]},
            "rent": {"recorded_monthly_cents": rent,
                     "delta_monthly_cents": rent - baseline_rent if rent is not None and baseline_rent is not None else None,
                     "currency": "USD" if rent is not None else None,
                     "fees_monthly_cents": None, "all_in_monthly_cents": None,
                     "basis": "historical_recorded_asking_rent" if rent is not None else "unknown"},
            "provenance": {
                "catalog_scenario_hash": fingerprint(source), "frozen_baseline_scenario_hash": baseline_hash,
                "home_source": source["home"].get("source", "unknown"),
                "calculation": "Only home is substituted into the frozen baseline scenario; basket, stores, travel rules, wallet, and requested limits are fixed.",
                "current_availability_verified": False,
            },
        }

    counts = {key: 0 for key in ("invalid", "world_mismatch", "content_mismatch", "current_home", "duplicate_home")}
    compatible = []
    validated = []
    if supported:
        for raw in catalog:
            try:
                candidate = Scenario.model_validate(raw).model_dump(mode="json")
                fingerprint(candidate)  # Reject non-JSON/nonfinite extra metadata as an invalid catalog record.
            except (ValueError, TypeError):
                counts["invalid"] += 1
                continue
            validated.append(candidate)
        for candidate in sorted(validated, key=lambda value: (value["id"], fingerprint(value["home"]))):
            if candidate.get("world_id") != world_id or candidate.get("world_version") != world_version:
                counts["world_mismatch"] += 1
            elif canonical(_world(candidate)) != canonical(world_content):
                counts["content_mismatch"] += 1
            elif candidate["id"] == scenario["id"] or _same_home(scenario["home"], candidate["home"]):
                counts["current_home"] += 1
            elif any(_same_home(other["home"], candidate["home"]) for other in compatible):
                counts["duplicate_home"] += 1
            else:
                compatible.append(candidate)
    else:
        counts["world_mismatch"] = len(catalog)
    locations = [entry(candidate) for candidate in compatible[:MAX_OTHER_HOMES]]
    status = (
        "unsupported_world" if not supported else
        "needs_review" if wallet is not None and wallet.get("status") != "ready" else
        "ok" if locations else "no_compatible_homes"
    )
    return {
        "status": status, "constraints": constraints, "assignments": assignments,
        "world": {"id": world_id if supported else None, "version": world_version if supported else None,
                  "content_hash": fingerprint(world_content), "supported": bool(supported)},
        "wallet_summary": _wallet_summary(wallet), "baseline": entry(scenario, current=True),
        "locations": locations,
        "catalog_summary": {"supplied_count": len(catalog), "compatible_distinct_home_count": len(compatible),
                            "returned_home_count": len(locations), "max_other_homes": MAX_OTHER_HOMES,
                            "truncated": len(compatible) > MAX_OTHER_HOMES, "skipped": counts},
        "assumptions": [
            "This compares a single shopping trip in a simulation. It does not recommend moving or infer household income.",
            "The current baseline is the saved session's frozen home, even if the catalog now contains a refreshed listing or a reused scenario ID.",
            "Candidate homes require matching world ID, world version, and actual basket, store, price, and travel-rule content. Legacy worlds are not silently mapped to a newer catalog.",
            "Same-assignment results keep each product at its selected store. Optimized plans may use different stores and are shown separately, under the same complete basket and limits.",
            "The frozen wallet and projection inputs stay fixed. Current cash, its reserve, and available travel modes remain hard boundaries; future income cannot fund a trip now.",
            "Recorded monthly asking rent is historical context, separate from per-trip shopping totals. It is shown only with explicit monthly-rent basis and USD metadata; no currency conversion or sale-price inference is used.",
            "Additional fees and all-in housing costs are unknown. Homes can differ in size, bedrooms, condition, and other unmodeled qualities; rent differences are not a like-for-like affordability conclusion.",
            "Current listing availability is unverified. No session, choice, wallet, or catalog record is changed by this report.",
        ],
    }


def register_location_routes(app, store: LabStore, scenarios: dict):
    bearer = HTTPBearer(auto_error=False)

    @app.post("/api/v1/sessions/{session_id}/locations")
    def locations(
        session_id: UUID, body: LocationComparisonRequest,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        token = credentials.credentials if credentials else None
        session = store.session(str(session_id), token)
        if not any(choice["stage"] == "initial" for choice in session["choices"]):
            raise LabConflict("Submit your initial choice before comparing home locations")
        anchor = session["choices"][-1]
        constraints = body.constraints.model_dump(mode="json") if body.constraints else anchor["evaluation"]["constraints"]
        assignments = body.assignments if body.assignments is not None else anchor["evaluation"]["assignments"]
        try:
            report = compare_locations(session["scenario"], constraints, assignments, scenarios)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, "Location comparison settings are invalid for this session") from None
        latest = store.session(str(session_id), token)["choices"][-1]
        return {**report, "anchor_choice_id": anchor["id"], "stale": latest["id"] != anchor["id"]}
