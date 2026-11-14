"""Acceptance checks for the shipped exercise, not real-world market claims."""
import json
from pathlib import Path
from spending_api.lab_engine import recommend
from spending_api.lab_models import Catalog

def test_shipped_scenarios_offer_real_money_time_tradeoffs():
    raw=json.loads(Path("data/scenarios.json").read_text(encoding="utf-8"))
    catalog=Catalog.model_validate(raw)
    assert len(catalog.scenarios)==3
    for entry in catalog.scenarios:
        scenario=entry.model_dump(mode="json")
        assert scenario["home"]["source"]=="costar"
        assert scenario["home"]["source_record_id"]
        assert "fictional" in scenario["provenance"]["stores"].lower()
        assert "simulated" in scenario["provenance"]["prices"].lower()
        result=recommend(scenario,scenario["defaults"])
        assert len(result["plans"])>=2
        assert all(plan["feasible"] for plan in result["plans"])
        assert result["plans"][0]["total_cost_cents"] < result["plans"][-1]["total_cost_cents"]
        assert result["plans"][0]["total_minutes"] > result["plans"][-1]["total_minutes"]
