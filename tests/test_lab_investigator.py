"""Independent brute-force checks for counterfactuals and a fake tool-using model."""

import copy
import itertools
import math

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from spending_api.lab_engine import evaluate
from spending_api.lab_investigator import investigate_decision, investigation_tools, run_investigation_agent


@pytest.fixture
def scenario():
    return {
        "id": "counterfactual", "version": "v1", "name": "Counterfactual world",
        "description": "Fixed groceries, independent oracle",
        "home": {"label": "Home", "latitude": 0.0, "longitude": 0.0},
        "defaults": {"budget_cents": 1300, "time_budget_minutes": 20, "travel_mode": "walk", "max_stores": 1},
        "basket": [
            {"id": "milk", "name": "Milk", "quantity": 2, "unit": "gallon", "category": "Dairy"},
            {"id": "bread", "name": "Bread", "quantity": 1, "unit": "loaf", "category": "Bakery"},
        ],
        "stores": [
            {"id": "near", "name": "Near", "latitude": 0.0, "longitude": 0.001,
             "shopping_minutes": 5, "prices": {"milk": 500, "bread": 500}},
            {"id": "value", "name": "Value", "latitude": 0.0, "longitude": 0.01,
             "shopping_minutes": 8, "prices": {"milk": 300, "bread": 600}},
            {"id": "bakery", "name": "Bakery", "latitude": 0.01, "longitude": 0.0,
             "shopping_minutes": 4, "prices": {"milk": 800, "bread": 100}},
        ],
        "travel": {"method": "modeled_distance", "road_factor": 1.0, "walk_kmh": 5.0,
                   "drive_kmh": 30.0, "drive_cost_cents_per_km": 100,
                   "drive_parking_minutes_per_stop": 3},
        "provenance": {"prices": "Simulated", "travel": "Modeled"},
    }


def brute_plans(scenario, constraints):
    products = [p["id"] for p in scenario["basket"]]
    stores = [s["id"] for s in scenario["stores"]]
    return [evaluate(scenario, dict(zip(products, assignment)), constraints)
            for assignment in itertools.product(stores, repeat=len(products))
            if len(set(assignment)) <= constraints["max_stores"]]


@pytest.mark.parametrize("mode", ["walk", "drive"])
@pytest.mark.parametrize("max_stores", [1, 2])
@pytest.mark.parametrize("budget,time", [(1300, 20), (500, 5), (800, 100), (1600, 5), (2000, 100)])
def test_exact_minima_match_independent_exhaustive_assignments(scenario, max_stores, budget, time, mode):
    constraints = {**scenario["defaults"], "budget_cents": budget, "time_budget_minutes": time, "max_stores": max_stores, "travel_mode": mode}
    report = investigate_decision(scenario, constraints)
    all_plans = brute_plans(scenario, constraints)
    can_time = [p for p in all_plans if p["total_minutes"] <= time]
    can_money = [p for p in all_plans if p["total_cost_cents"] <= budget]
    assert report["minimum_requirements"]["minimum_budget_cents"] == (min(p["total_cost_cents"] for p in can_time) if can_time else None)
    assert report["minimum_requirements"]["minimum_time_minutes"] == (min(p["total_minutes"] for p in can_money) if can_money else None)
    assert (report["status"] == "feasible") == any(p["feasible"] for p in all_plans)
    for proposal in report["alternatives"]:
        plan = evaluate(scenario, proposal["plan"]["assignments"], proposal["constraints"])
        assert plan["feasible"]
        assert set(plan["assignments"]) == {p["id"] for p in scenario["basket"]}
        assert proposal["required_minutes"] == plan["total_minutes"]


def test_smallest_single_limit_changes_and_whole_minute_controls(scenario):
    report = investigate_decision(scenario, scenario["defaults"])
    by_kind = {p["kind"]: p for p in report["alternatives"]}
    budget = by_kind["budget"]
    time = by_kind["time"]
    assert budget["changes"] == {"budget_cents": 1500}
    cheaper = {**budget["constraints"], "budget_cents": 1499}
    assert not any(p["feasible"] for p in brute_plans(scenario, cheaper))
    assert time["changes"] == {"time_budget_minutes": math.ceil(time["required_minutes"])}
    less_time = {**time["constraints"], "time_budget_minutes": math.nextafter(time["required_minutes"], -math.inf)}
    assert not any(p["feasible"] for p in brute_plans(scenario, less_time))
    assert report["question"]["id"] == "money_or_time"
    evidence = {e["id"] for e in report["evidence"]}
    assert all(set(p["evidence_ids"]) <= evidence for p in report["alternatives"])


def test_changing_assignments_can_fix_choice_without_relaxing_limits(scenario):
    constraints = {**scenario["defaults"], "budget_cents": 1600}
    report = investigate_decision(scenario, constraints, assignments={"milk": "value", "bread": "value"})
    assert not report["selected_plan"]["feasible"]
    assert report["status"] == "feasible"
    assert not report["alternatives"]
    assert "another assignment" in report["explanation"]
    assert report["feasible_plans"][0]["assignments"] == {"bread": "near", "milk": "near"}


def test_protected_constraints_remove_actions_but_keep_minimum_evidence(scenario):
    report = investigate_decision(scenario, scenario["defaults"], protected_constraints=["budget_cents", "time_budget_minutes", "max_stores"])
    assert report["status"] == "no_feasible_plan"
    assert not report["alternatives"]
    assert report["minimum_requirements"]["minimum_budget_cents"] == 1500
    with pytest.raises(ValueError, match="Unknown protected"):
        investigate_decision(scenario, scenario["defaults"], protected_constraints=["money"])


def test_second_store_is_explicit_and_protectable(scenario):
    constraints = {**scenario["defaults"], "budget_cents": 800, "time_budget_minutes": 100}
    report = investigate_decision(scenario, constraints)
    proposal = next(p for p in report["alternatives"] if p["kind"] == "stops")
    assert proposal["changes"] == {"max_stores": 2}
    assert proposal["plan"]["total_cost_cents"] == 700  # 2 milk at 300 plus bread at 100.
    protected = investigate_decision(scenario, constraints, protected_constraints=["max_stores"])
    assert all("max_stores" not in p["changes"] for p in protected["alternatives"])


def test_combined_limits_are_not_misrepresented_as_one_change(scenario):
    constraints = {**scenario["defaults"], "budget_cents": 500, "time_budget_minutes": 5}
    report = investigate_decision(scenario, constraints)
    assert report["alternatives"]
    assert all(p["kind"] == "combined" and len(p["changes"]) >= 2 for p in report["alternatives"])
    assert report["minimum_requirements"] == {"minimum_budget_cents": None, "minimum_time_minutes": None}
    assert all(p["constraints"]["travel_mode"] == "walk" for p in report["alternatives"])


def test_wallet_is_hard_current_cash_cap_not_future_income(scenario):
    wallet = {"status": "ok", "spendable_now_cents": 1250, "shopping_allowance_cents": 9000,
              "expected_income_cents": 10000, "reserve_cents": 500,
              "allowed_travel_modes": ["walk"], "customer_id": "private"}
    report = investigate_decision(scenario, scenario["defaults"], wallet_summary=wallet)
    assert all(p["plan"]["total_cost_cents"] <= 1250 for p in report["alternatives"])
    assert all(p["kind"] != "budget" for p in report["alternatives"])
    assert "customer_id" not in report["wallet"]
    money_evidence = next(e for e in report["evidence"] if e["kind"] == "minimum_budget")
    assert money_evidence["values"]["wallet_shortfall_cents"] == 250
    assert report["wallet"]["reserve_cents"] == 500
    # Reserve has already been accounted for by the wallet; do not subtract it twice.
    assert any(p["plan"]["total_cost_cents"] == 1200 for p in report["alternatives"])


@pytest.mark.parametrize("wallet", [
    {"status": "needs_review", "spendable_now_cents": None},
    {"status": "ok", "spendable_now_cents": None},
    {"status": "needs_review", "spendable_now_cents": 5000},
])
def test_unknown_wallet_never_claims_affordability(scenario, wallet):
    constraints = {**scenario["defaults"], "budget_cents": 2000, "time_budget_minutes": 100}
    report = investigate_decision(scenario, constraints, wallet_summary=wallet, assignments={"milk": "near", "bread": "near"})
    assert report["status"] == "needs_review"
    assert not report["feasible_plans"] and not report["alternatives"]
    assert not report["selected_plan"]["feasible"]


def test_unavailable_transport_is_a_resource_limit(scenario):
    constraints = {**scenario["defaults"], "budget_cents": 2000, "time_budget_minutes": 100, "travel_mode": "drive"}
    wallet = {"status": "ok", "spendable_now_cents": 5000, "allowed_travel_modes": ["walk"]}
    report = investigate_decision(scenario, constraints, allowed_travel_modes=["walk", "drive"], wallet_summary=wallet)
    assert report["allowed_travel_modes"] == ["walk"]
    assert report["status"] == "changes_available"
    assert all(p["changes"]["travel_mode"] == "walk" for p in report["alternatives"])
    protected = investigate_decision(scenario, constraints, allowed_travel_modes=["walk", "drive"], wallet_summary=wallet, protected_constraints=["travel_mode"])
    assert not protected["alternatives"]
    no_modes = investigate_decision(scenario, constraints, allowed_travel_modes=[])
    assert no_modes["status"] == "no_feasible_plan" and not no_modes["alternatives"]


def test_app_bounds_do_not_turn_impossible_minimum_into_apply_action(scenario):
    for store in scenario["stores"]:
        store["prices"] = {"milk": 1_000_000, "bread": 1_000_000}
    report = investigate_decision(scenario, scenario["defaults"])
    assert report["minimum_requirements"]["minimum_budget_cents"] == 3_000_000
    assert not report["alternatives"]
    for store in scenario["stores"]:
        store["prices"] = {"milk": 100, "bread": 100}
        store["longitude"] = 1
    too_slow = investigate_decision(scenario, scenario["defaults"])
    assert too_slow["minimum_requirements"]["minimum_time_minutes"] > 1440
    assert not too_slow["alternatives"]


def test_inputs_and_snapshot_tools_are_isolated(scenario):
    constraints = scenario["defaults"]
    original = copy.deepcopy(scenario)
    report = investigate_decision(scenario, constraints)
    assert scenario == original
    assert report == investigate_decision(scenario, constraints)
    tools = {t.name: t for t in investigation_tools(report)}
    result = tools["inspect_current_situation"].invoke({})
    result["constraints"]["budget_cents"] = 1
    assert tools["inspect_current_situation"].invoke({})["constraints"] == constraints
    with pytest.raises(ValueError, match="known alternative"):
        tools["compare_plans"].invoke({"alternative_ids": ["invented"]})
    with pytest.raises(ValueError):
        tools["compare_plans"].invoke({"alternative_ids": ["x"] * 4})


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    calls: int = 0
    tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-investigation-test"

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [t.get("function", t).get("name", "") if isinstance(t, dict) else t.name for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


def tool_message(name, args, call="call"):
    return AIMessage(content="", tool_calls=[{"id": call, "name": name, "args": args}])


def test_optional_langchain_agent_uses_read_only_tools_and_validated_ids(scenario):
    report = investigate_decision(scenario, scenario["defaults"])
    choice = report["alternatives"][0]
    model = ScriptedModel(responses=[
        tool_message("inspect_current_situation", {}),
        tool_message("test_allowed_change", {"kind": choice["kind"]}, "test"),
        tool_message("compare_plans", {"alternative_ids": [choice["id"]]}, "compare"),
        tool_message("InvestigationSelection", {"evidence_ids": choice["evidence_ids"],
                     "alternative_ids": [choice["id"]], "question_id": "money_or_time"}, "answer"),
    ])
    result = run_investigation_agent(report, model, "I would rather spend a little more than walk longer.")
    assert result["mode"] == "langchain"
    assert model.calls == 4
    assert result["highlighted_alternative_ids"] == [choice["id"]]
    assert result["question"]["id"] == "money_or_time"
    assert result["alternatives"] == report["alternatives"]
    assert result["evidence"] == report["evidence"]
    assert set(model.tool_names) == {"inspect_current_situation", "test_allowed_change", "compare_plans", "InvestigationSelection"}


def test_optional_agent_rejects_hallucinated_ids_and_bounds_loops(scenario):
    report = investigate_decision(scenario, scenario["defaults"])
    bad = ScriptedModel(responses=[tool_message("InvestigationSelection", {"alternative_ids": ["invented"]})])
    result = run_investigation_agent(report, bad)
    assert result["mode"] == "fallback"
    assert result["alternatives"] == report["alternatives"]
    loop = ScriptedModel(responses=[tool_message("inspect_current_situation", {})])
    limited = run_investigation_agent(report, loop)
    assert limited["mode"] == "fallback"
    assert loop.calls == 4
    assert limited["constraints"] == report["constraints"]


def test_optional_agent_bounds_tool_fanout(scenario):
    report = investigate_decision(scenario, scenario["defaults"])
    response = AIMessage(content="", tool_calls=[
        {"id": f"call_{i}", "name": "inspect_current_situation", "args": {}} for i in range(7)
    ])
    model = ScriptedModel(responses=[response])
    result = run_investigation_agent(report, model)
    assert result["mode"] == "fallback"
    assert model.calls == 1
    assert result["alternatives"] == report["alternatives"]
