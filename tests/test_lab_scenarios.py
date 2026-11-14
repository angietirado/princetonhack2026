"""Spatial and provenance acceptance checks for the fixed Houston shopping world."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import build_scenarios as generator
from spending_api.lab_engine import evaluate, recommend
from spending_api.lab_models import Catalog


def shipped():
    raw = json.loads(Path("data/scenarios.json").read_text(encoding="utf-8"))
    return [scenario.model_dump(mode="json") for scenario in Catalog.model_validate(raw).scenarios]


@pytest.fixture
def rental_rows():
    # Minimal public source records with the same factual fields as the selected kit records.
    facts = [
        (240486874, 223423787, 500020427889, 2300, "1010 Rosine Street", "49",
         29.75684, -95.399208, 1300, "2026-11-04T00:00:00+00:00"),
        (232112148, 218287553, 500020510363, 3200, "732 Ruthven Street", None,
         29.755274, -95.3759, 2597, "2026-08-06T00:00:00+00:00"),
        (238679692, 222773404, 86864, 2499, "2323 Polk Street", "205",
         29.748008, -95.354675, 1312, "2026-10-21T00:00:00+00:00"),
    ]
    return [
        {
            "id": record_id, "residentialListingId": residential_id, "propertyId": property_id,
            "transactionType": 2, "listingStatus": 1, "monetaryUnit": 1,
            "permissionLevels": {"homes": 10}, "currentPrice": rent,
            "listDate": list_date, "domDate": None,
            "property": {
                "address": {"city": "Houston", "state": "TX", "fullStreetAddress": address,
                            "unitNumber": unit, "latitude": latitude, "longitude": longitude},
                "bedsMin": 2, "bedsMax": 2, "sqftMin": sqft, "sqftMax": sqft,
            },
        }
        for record_id, residential_id, property_id, rent, address, unit,
        latitude, longitude, sqft, list_date in facts
    ]


def write_source(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_homes_share_one_fixed_world_and_identical_default_constraints():
    scenarios = shipped()
    assert {scenario["id"] for scenario in scenarios} == {
        "houston-rental-west", "houston-rental-central", "houston-rental-east"
    }
    assert len({scenario["world_id"] for scenario in scenarios}) == 1
    assert len({scenario["world_version"] for scenario in scenarios}) == 1
    assert len({scenario["version"] for scenario in scenarios}) == 3
    assert len({(s["home"]["latitude"], s["home"]["longitude"]) for s in scenarios}) == 3
    reference = scenarios[0]
    for scenario in scenarios:
        assert scenario["stores"] == reference["stores"]
        assert scenario["basket"] == reference["basket"]
        assert scenario["travel"] == reference["travel"]
        assert scenario["defaults"] == {
            "budget_cents": 6500, "time_budget_minutes": 45, "travel_mode": "drive", "max_stores": 2
        }
        assert len(scenario["stores"]) == 5
        assert all(store["id"].startswith(scenario["world_id"]) for store in scenario["stores"])


def test_housing_is_public_recorded_monthly_rent_with_unknown_fees():
    expected = {
        "240486874": (230000, "49", 1300, "2026-11-04T00:00:00+00:00"),
        "232112148": (320000, None, 2597, "2026-08-06T00:00:00+00:00"),
        "238679692": (249900, "205", 1312, "2026-10-21T00:00:00+00:00"),
    }
    for scenario in shipped():
        home = scenario["home"]
        rent, unit, sqft, listed = expected[home["source_record_id"]]
        assert home["source"] == "costar"
        assert home["source_dataset"] == "residential-listings"
        assert home["source_permission_homes"] == 10
        assert home["source_transaction_type"] == 2
        assert home["source_listing_status"] in [1, 2]
        assert home["source_monetary_unit"] == 1
        assert home["housing_price_basis"] == "monthly_rent"
        assert home["monthly_rent_cents"] == rent
        assert home["unit_number"] == unit
        assert home["bedrooms_min"] == home["bedrooms_max"] == 2
        assert home["living_area_sqft_min"] == home["living_area_sqft_max"] == sqft
        assert home["source_listing_date"] == listed
        assert home["current_availability_verified"] is False
        assert home["source_snapshot_date"] is None
        assert home["additional_monthly_fees_cents"] is None
        assert home["additional_monthly_fees_status"] == "unknown"
        assert home["all_in_monthly_housing_cost_cents"] is None
        assert len(home["source_fingerprint"]) == 64
        assert "fictional" in scenario["provenance"]["stores"].lower()
        assert "simulated" in scenario["provenance"]["prices"].lower()


def test_same_basket_and_store_changes_cost_time_and_feasibility_by_home():
    scenarios = shipped()
    constraints = {**scenarios[0]["defaults"], "time_budget_minutes": 20, "max_stores": 1}
    assignments = {item["id"]: "houston-inner-loop-grove" for item in scenarios[0]["basket"]}
    results = [evaluate(scenario, assignments, constraints) for scenario in scenarios]
    assert len({result["basket_cost_cents"] for result in results}) == 1
    assert len({result["total_minutes"] for result in results}) == 3
    assert len({result["transport_cost_cents"] for result in results}) == 3
    assert len({result["total_cost_cents"] for result in results}) == 3
    assert [result["feasible"] for result in results] == [False, True, False]
    assert all(result["route"] == ["home", "houston-inner-loop-grove", "home"] for result in results)


def test_walk_only_feasibility_changes_without_moving_stores():
    scenarios = shipped()
    constraints = {**scenarios[0]["defaults"], "time_budget_minutes": 20,
                   "travel_mode": "walk", "max_stores": 1}
    assignments = {item["id"]: "houston-inner-loop-corner" for item in scenarios[0]["basket"]}
    results = [evaluate(scenario, assignments, constraints) for scenario in scenarios]
    assert [result["feasible"] for result in results] == [True, False, False]
    assert all(result["transport_cost_cents"] == 0 for result in results)


def test_shipped_defaults_offer_feasible_money_time_tradeoffs():
    for scenario in shipped():
        result = recommend(scenario, scenario["defaults"])
        assert len(result["plans"]) >= 2
        assert all(plan["feasible"] for plan in result["plans"])
        assert result["plans"][0]["total_cost_cents"] < result["plans"][-1]["total_cost_cents"]
        assert result["plans"][0]["total_minutes"] > result["plans"][-1]["total_minutes"]


def test_generation_is_repeatable_and_independent_of_source_record_order(tmp_path, rental_rows):
    source = write_source(tmp_path / "source.jsonl", rental_rows)
    first = generator.build(source)
    assert generator.build(source) == first
    write_source(source, reversed(rental_rows))
    assert generator.build(source) == first
    output = tmp_path / "catalog.json"
    generator.write_catalog(first, output)
    original_bytes = output.read_bytes()
    generator.write_catalog(generator.build(source), output)
    assert output.read_bytes() == original_bytes


def test_home_change_preserves_world_but_changes_only_its_scenario_version(tmp_path, rental_rows):
    source = write_source(tmp_path / "source.jsonl", rental_rows)
    before = generator.build(source)["scenarios"]
    rental_rows[0]["property"]["address"]["longitude"] = -95.398
    after = generator.build(write_source(source, rental_rows))["scenarios"]
    assert before[0]["world_version"] == after[0]["world_version"]
    assert before[0]["stores"] == after[0]["stores"]
    assert before[0]["version"] != after[0]["version"]
    assert before[1:] == after[1:]


def test_changed_world_has_new_version_for_every_home(tmp_path, rental_rows, monkeypatch):
    source = write_source(tmp_path / "source.jsonl", rental_rows)
    before = generator.build(source)["scenarios"]
    stores = deepcopy(generator.STORES)
    stores[0][5][0] += 1
    monkeypatch.setattr(generator, "STORES", stores)
    after = generator.build(source)["scenarios"]
    assert len({scenario["world_version"] for scenario in after}) == 1
    for old, new in zip(before, after, strict=True):
        assert old["home"] == new["home"]
        assert old["world_version"] != new["world_version"]
        assert old["version"] != new["version"]


@pytest.mark.parametrize("field,value", [
    ("transactionType", 1), ("listingStatus", 4), ("monetaryUnit", 0),
    ("permissionLevels", {"homes": 215}), ("currentPrice", None),
    ("currentPrice", 0), ("currentPrice", -100), ("currentPrice", 2300.001),
    ("currentPrice", True), ("listDate", "not a date"),
])
def test_selected_rentals_fail_closed_when_source_facts_are_invalid(
    tmp_path, rental_rows, field, value
):
    rental_rows[0][field] = value
    with pytest.raises(ValueError):
        generator.build(write_source(tmp_path / "source.jsonl", rental_rows))


@pytest.mark.parametrize("field,value", [("latitude", None), ("longitude", float("inf")),
                                        ("city", "Los Angeles")])
def test_invalid_selected_geography_is_rejected(tmp_path, rental_rows, field, value):
    rental_rows[0]["property"]["address"][field] = value
    with pytest.raises(ValueError):
        generator.build(write_source(tmp_path / "source.jsonl", rental_rows))


def test_missing_or_duplicate_record_never_silently_selects_another_home(tmp_path, rental_rows):
    source = tmp_path / "source.jsonl"
    with pytest.raises(ValueError, match="Missing selected"):
        generator.build(write_source(source, rental_rows[:2]))
    with pytest.raises(ValueError, match="Duplicate selected"):
        generator.build(write_source(source, rental_rows + rental_rows[:1]))


@pytest.mark.parametrize("failure", ["missing_file", "missing_record", "malformed"])
def test_source_failure_leaves_existing_catalog_unchanged(tmp_path, rental_rows, failure):
    source = tmp_path / "source.jsonl"
    if failure == "missing_record":
        write_source(source, rental_rows[:2])
    elif failure == "malformed":
        source.write_text("{not json}\n", encoding="utf-8")
    output = tmp_path / "catalog.json"
    output.write_bytes(b"previous catalog must survive")
    with pytest.raises(SystemExit) as error:
        generator.main(["--source", str(source), "--output", str(output)])
    assert error.value.code == 2
    assert output.read_bytes() == b"previous catalog must survive"
    assert not list(tmp_path.glob(".catalog.json.*.tmp"))


def test_failed_atomic_replace_preserves_existing_catalog_and_removes_temp(tmp_path, monkeypatch):
    output = tmp_path / "catalog.json"
    output.write_bytes(b"existing catalog")
    def fail_replace(_source, _target):
        raise OSError("Simulated write failure")
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="Simulated write failure"):
        generator.write_catalog({"schema_version": 1, "scenarios": []}, output)
    assert output.read_bytes() == b"existing catalog"
    assert not list(tmp_path.glob(".catalog.json.*.tmp"))
