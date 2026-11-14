"""Small exhaustive shopping planner. No model or external API participates in arithmetic."""

import hashlib
import itertools
import json
import math
from decimal import ROUND_HALF_UP, Decimal

from spending_api.lab_models import Constraints, Scenario


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def distance_km(left: dict, right: dict) -> float:
    lat1, lat2 = math.radians(left["latitude"]), math.radians(right["latitude"])
    dlat = lat2 - lat1
    dlon = math.radians(right["longitude"] - left["longitude"])
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


def evaluate(scenario: dict, assignments: dict[str, str], constraints: dict) -> dict:
    # Validate direct callers too: chat and command-line use the same contract.
    scenario = Scenario.model_validate(scenario).model_dump(mode="json")
    constraints = Constraints.model_validate(constraints).model_dump(mode="json")
    products = {item["id"]: item for item in scenario["basket"]}
    stores = {store["id"]: store for store in scenario["stores"]}
    if set(assignments) != set(products):
        raise ValueError("Assign every basket item exactly once")
    if any(store not in stores for store in assignments.values()):
        raise ValueError("A selected store is not part of this scenario")
    selected = sorted(set(assignments.values()))
    if not selected or len(selected) > 2:
        raise ValueError("Choose one or two stores for the complete basket")

    price = sum(
        stores[store_id]["prices"][product_id] * products[product_id]["quantity"]
        for product_id, store_id in assignments.items()
    )
    home = scenario["home"]
    travel = scenario["travel"]

    def route_distance(order):
        points = [home, *(stores[item] for item in order), home]
        return sum(distance_km(a, b) for a, b in itertools.pairwise(points)) * travel["road_factor"]

    distance, order = min(
        (route_distance(order), order) for order in itertools.permutations(selected)
    )
    driving = constraints["travel_mode"] == "drive"
    speed = travel["drive_kmh"] if driving else travel["walk_kmh"]
    travel_minutes = distance / speed * 60
    parking = len(selected) * travel["drive_parking_minutes_per_stop"] if driving else 0
    shopping_minutes = sum(stores[item]["shopping_minutes"] for item in selected) + parking
    total_minutes = travel_minutes + shopping_minutes
    transport = int(
        (Decimal(str(distance)) * Decimal(str(travel["drive_cost_cents_per_km"])))
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    ) if driving else 0
    total = price + transport
    violations = []
    if total > constraints["budget_cents"]:
        violations.append("This plan exceeds the money budget.")
    if total_minutes > constraints["time_budget_minutes"]:
        violations.append("This plan exceeds the time budget.")
    if len(selected) > constraints["max_stores"]:
        violations.append("This plan exceeds the maximum number of stores.")
    return {
        "basket_cost_cents": price,
        "transport_cost_cents": transport,
        "total_cost_cents": total,
        "travel_minutes": travel_minutes,
        "shopping_minutes": shopping_minutes,
        "total_minutes": total_minutes,
        "distance_km": distance,
        "feasible": not violations,
        "violations": violations,
        "store_ids": list(order),
        "route": ["home", *order, "home"],
        "assignments": dict(sorted(assignments.items())),
        "constraints": constraints,
    }


def recommend(scenario: dict, constraints: dict) -> dict:
    scenario = Scenario.model_validate(scenario).model_dump(mode="json")
    constraints = Constraints.model_validate(constraints).model_dump(mode="json")
    stores = sorted(scenario["stores"], key=lambda store: store["id"])
    candidates = {}
    for count in range(1, min(constraints["max_stores"], len(stores)) + 1):
        for subset in itertools.combinations(stores, count):
            assignments = {
                item["id"]: min(subset, key=lambda s: (s["prices"][item["id"]], s["id"]))["id"]
                for item in scenario["basket"]
            }
            key = canonical(assignments)
            if key not in candidates:
                candidates[key] = evaluate(scenario, assignments, constraints)

    feasible = [item for item in candidates.values() if item["feasible"]]
    feasible.sort(key=lambda item: (
        item["total_cost_cents"], item["total_minutes"], canonical(item["assignments"])
    ))
    frontier = []
    for item in feasible:
        if any(
            prior["total_cost_cents"] <= item["total_cost_cents"]
            and prior["total_minutes"] <= item["total_minutes"]
            for prior in frontier
        ):
            continue
        frontier.append(item)
    # Cheapest-first sorting makes every next frontier point faster.
    if len(frontier) > 8:
        indexes = sorted({round(i * (len(frontier) - 1) / 7) for i in range(8)})
        frontier = [frontier[index] for index in indexes]
    plans = []
    for index, item in enumerate(frontier):
        label = "Lowest cost" if index == 0 else "Least time" if index == len(frontier) - 1 else "Balanced"
        plans.append({
            **item,
            "id": fingerprint({"assignments": item["assignments"], "constraints": constraints})[:16],
            "label": label,
        })
    return {
        "status": "ok" if plans else "no_feasible_plan",
        "plans": plans,
        "evaluated_plan_count": len(candidates),
        "feasible_plan_count": len(feasible),
        "notice": (
            "Alternatives use the same complete basket and simulated prices and travel. "
            "No budget or time constraint has been relaxed."
        ),
    }

