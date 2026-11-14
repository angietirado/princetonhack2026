"""Offline fixtures exercise catalog semantics without copying the supplied data."""

import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from spending_api.costar import FILES, CostarCatalog, CostarSearchCriteria


def property_row(property_id=101, plan_id=1):
    return {
        "id": property_id,
        "primaryAddress": {"state": "TX", "city": "Houston"},
        "propertyApartment": {
            "models": [{"id": plan_id}],
        },
        "attachments": [],
        "contacts": [{"email": "secret-contact@example.com"}],
    }


def listing_row(property_id=101, plan_id=1, listing_id=501):
    return {
        "id": listing_id,
        "propertyId": property_id,
        "address": {"state": "TX", "city": "Houston"},
        "buildingName": "Synthetic Apartments",
        "lifestyleType": 1,
        "apartmentModels": [
            {
                "id": plan_id,
                "name": "One Bedroom",
                "numBeds": 1,
                "minRent": 1300,
                "maxRent": 1600,
                "monetaryUnit": 1,
                "rentPricingType": 1,
                "numUnitsAvailable": 2,
                "minSquareFeet": 700,
            }
        ],
    }


def write_kit(directory, *, properties=None, listings=None, catalog=None):
    (directory / "data").mkdir(parents=True, exist_ok=True)
    properties = [property_row()] if properties is None else properties
    listings = [listing_row()] if listings is None else listings
    for relative, rows in ((FILES[0], listings), (FILES[1], properties)):
        (directory / relative).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    return directory


@pytest.fixture
def kit(tmp_path):
    return write_kit(tmp_path)


def test_exact_join_rent_and_evidence_are_snapshot_only(kit):
    result = CostarCatalog(kit, snapshot_date="2026-11-12").search({})
    assert result["status"] == "ok"
    assert result["source_kind"] == "costar_local_snapshot"
    assert result["snapshot_date_basis"] == "server_declared"
    assert result["snapshot_date"] == "2026-11-12"
    (row,) = result["results"]
    assert (row["property_id"], row["floor_plan_id"], row["listing_id"]) == (101, 1, 501)
    assert row["asking_rent"] == {
        "minimum": 1300,
        "maximum": 1600,
        "currency": "USD",
        "period": "month",
        "basis": "per_unit",
    }
    assert row["recorded_units_available"] == 2
    assert row["availability_is_live"] is False
    assert row["price_range_within_budget"] is True
    assert row["evidence"][0] == {
        "file": FILES[0],
        "line": 1,
        "listing_id": 501,
        "floor_plan_id": 1,
    }
    assert "secret-contact" not in json.dumps(result)
    assert str(kit) not in json.dumps(result)
    assert result["coverage"]["current_availability_verified"] is False
    assert result["coverage"]["market_coverage_complete"] is False
    for relative in FILES:
        assert (
            result["coverage"]["source_file_sha256"][relative]
            == hashlib.sha256((kit / relative).read_bytes()).hexdigest()
        )


@pytest.mark.parametrize(
    "value",
    [
        {"limit": 0},
        {"limit": 21},
        {"limit": True},
        {"limit": "5"},
        {"bedrooms": -1},
        {"bedrooms": 7},
        {"bedrooms": 1.0},
        {"max_monthly_rent_usd": 0},
        {"max_monthly_rent_usd": True},
        {"min_square_feet": 10001},
        {"min_square_feet": None},
        {"state": "NY"},
        {"state": "tx"},
        {"city": " "},
        {"city": "x\ny"},
        {"city": 123},
        {"city": "x" * 81},
        {"require_floor_plan_model": 1},
        {"include_specialized_housing": "false"},
        {"data_dir": "/other"},
        {"customer_id": "other"},
        {"source": "live"},
    ],
)
def test_strict_request_validation_before_reads(value, monkeypatch):
    catalog = CostarCatalog("unused")
    monkeypatch.setattr(catalog, "_load", lambda: pytest.fail("invalid request read source"))
    with pytest.raises(ValidationError):
        catalog.search(value)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("minRent", 0, "asking_rent_missing"),
        ("minRent", None, "asking_rent_missing"),
        ("minRent", True, "asking_rent_missing"),
        ("minRent", "1300", "asking_rent_missing"),
        ("maxRent", 1200, "inconsistent_rent_range"),
        ("monetaryUnit", 0, "currency_unknown_or_not_usd"),
        ("monetaryUnit", 2, "currency_unknown_or_not_usd"),
        ("monetaryUnit", True, "currency_unknown_or_not_usd"),
        ("rentPricingType", 2, "rent_not_per_unit"),
        ("rentPricingType", 3, "rent_not_per_unit"),
        ("rentPricingType", True, "rent_not_per_unit"),
        ("numUnitsAvailable", 0, "no_positive_recorded_availability"),
        ("numUnitsAvailable", None, "no_positive_recorded_availability"),
        ("numUnitsAvailable", True, "no_positive_recorded_availability"),
        ("numBeds", True, "bedrooms_do_not_match"),
    ],
)
def test_unknown_or_wrong_rent_basis_is_excluded(tmp_path, field, value, reason):
    listing = listing_row()
    listing["apartmentModels"][0][field] = value
    result = CostarCatalog(write_kit(tmp_path, listings=[listing])).search({})
    assert result["status"] == "ok"
    assert result["results"] == []
    assert result["coverage"]["exclusions"] == {reason: 1}


@pytest.mark.parametrize("high", [2000, 0, None, True, "1600"])
def test_minimum_only_budget_match_retains_uncertainty(tmp_path, high):
    listing = listing_row()
    listing["apartmentModels"][0]["maxRent"] = high
    result = CostarCatalog(write_kit(tmp_path, listings=[listing])).search({})
    (row,) = result["results"]
    assert row["price_range_within_budget"] is False
    assert row["asking_rent"]["maximum"] == (high if type(high) is int and high > 0 else None)


def test_studio_and_city_filters(tmp_path):
    listing = listing_row()
    listing["apartmentModels"][0]["numBeds"] = 0
    catalog = CostarCatalog(write_kit(tmp_path, listings=[listing]))
    assert len(catalog.search({"bedrooms": 0, "city": " houSTON "})["results"]) == 1
    assert catalog.search({"bedrooms": 0, "city": "Los Angeles"})["results"] == []
    assert catalog.search({"bedrooms": 0, "state": "CA"})["results"] == []


@pytest.mark.parametrize("area", [0, None, True, "700"])
def test_missing_area_does_not_satisfy_minimum(tmp_path, area):
    listing = listing_row()
    listing["apartmentModels"][0]["minSquareFeet"] = area
    catalog = CostarCatalog(write_kit(tmp_path, listings=[listing]))
    assert catalog.search({})["results"][0]["minimum_square_feet"] is None
    result = catalog.search({"min_square_feet": 1})
    assert result["results"] == []
    assert result["coverage"]["exclusions"] == {"area_unknown_or_too_small": 1}


@pytest.mark.parametrize("audience", [2, 3, 4, 5, 6])
def test_specialized_housing_opt_in(tmp_path, audience):
    listing = listing_row()
    listing["lifestyleType"] = audience
    catalog = CostarCatalog(write_kit(tmp_path, listings=[listing]))
    assert catalog.search({})["results"] == []
    assert len(catalog.search({"include_specialized_housing": True})["results"]) == 1


@pytest.mark.parametrize("audience", [None, 0, 7, True, "1"])
def test_unknown_audience_stays_excluded_even_when_opted_in(tmp_path, audience):
    listing = listing_row()
    listing["lifestyleType"] = audience
    result = CostarCatalog(write_kit(tmp_path, listings=[listing])).search(
        {
            "include_specialized_housing": True,
        }
    )
    assert result["coverage"]["exclusions"] == {"housing_audience_unknown": 1}


def test_same_plan_id_on_different_property_cannot_join(tmp_path):
    result = CostarCatalog(write_kit(tmp_path, properties=[property_row(102)])).search({})
    assert result["coverage"]["exclusions"] == {"property_join_missing": 1}


def test_existing_property_wrong_plan_cannot_join(tmp_path):
    result = CostarCatalog(write_kit(tmp_path, properties=[property_row(plan_id=2)])).search({})
    assert result["coverage"]["exclusions"] == {"floor_plan_join_missing": 1}


def test_identical_duplicates_do_not_multiply_results(tmp_path):
    listing, prop = listing_row(), property_row()
    result = CostarCatalog(
        write_kit(
            tmp_path,
            listings=[listing, listing],
            properties=[prop, prop],
            catalog=[(101, "scan-one"), (101, "scan-one")],
        )
    ).search({})
    assert len(result["results"]) == 1
    assert result["coverage"]["raw_listing_floor_plan_count"] == 2
    assert result["coverage"]["unique_property_floor_plan_count"] == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_listings_for_same_property_plan_are_excluded(tmp_path, reverse):
    listings = [listing_row(), listing_row(listing_id=502)]
    if reverse:
        listings.reverse()
    result = CostarCatalog(write_kit(tmp_path, listings=listings)).search({})
    assert result["results"] == []
    assert result["coverage"]["exclusions"] == {"conflicting_floor_plan_listings": 1}


def test_conflicting_listing_id_cannot_move_to_other_property(tmp_path):
    result = CostarCatalog(
        write_kit(
            tmp_path,
            listings=[listing_row(), listing_row(property_id=102)],
            properties=[property_row(), property_row(102)],
        )
    ).search({})
    assert result["status"] == "unavailable"
    assert result["reason"] == "conflicting_listing_records"


def test_conflicting_property_records_fail_closed(tmp_path):
    prop = property_row()
    conflict = copy.deepcopy(prop)
    conflict["primaryAddress"]["state"] = "CA"
    result = CostarCatalog(write_kit(tmp_path, properties=[prop, conflict])).search({})
    assert result["status"] == "unavailable"
    assert result["reason"] == "conflicting_property_records"


def test_conflicting_listing_property_location_excluded(tmp_path):
    listing = listing_row()
    listing["address"]["state"] = "CA"
    result = CostarCatalog(write_kit(tmp_path, listings=[listing])).search({})
    assert result["coverage"]["exclusions"] == {"conflicting_location": 1}


@pytest.mark.parametrize(
    "limits",
    [
        {"max_file_bytes": 10},
        {"max_records": 1},
        {"max_floor_plans": 1},
        {"max_record_bytes": 10},
    ],
)
def test_source_limits_return_no_partial_results(tmp_path, limits):
    kit = write_kit(
        tmp_path,
        properties=[property_row(), property_row(102)],
        listings=[listing_row(), listing_row(102, listing_id=502)],
        catalog=[(101, "scan-one"), (102, "scan-one")],
    )
    result = CostarCatalog(kit, **limits).search({"limit": 1})
    assert result["status"] == "unavailable"
    assert result["reason"] == "catalog_limit_exceeded"
    assert result["results"] == []
    assert result["coverage"] == {}


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "100"])
def test_invalid_server_bounds_rejected(bad):
    with pytest.raises(ValueError):
        CostarCatalog(None, max_records=bad)


@pytest.mark.parametrize(
    "raw",
    [
        '{"id":101,"id":102}',
        "[]",
        "null",
        '{"id":NaN}',
        "{broken",
        '{"id":true}',
        '{"id":101,"propertyApartment":{"models":{}}}',
    ],
)
def test_malformed_catalog_is_generic_and_no_partial_result(kit, raw):
    (kit / FILES[1]).write_text(raw, encoding="utf-8")
    result = CostarCatalog(kit).search({})
    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_catalog"
    assert result["results"] == []


def test_unconfigured_missing_and_empty_catalog_are_distinct(tmp_path):
    assert CostarCatalog(None).search({})["reason"] == "not_configured"
    assert CostarCatalog(tmp_path).search({})["reason"] == "catalog_files_missing"
    result = CostarCatalog(write_kit(tmp_path, properties=[], listings=[], catalog=[])).search({})
    assert result["status"] == "ok"
    assert result["results"] == []
    assert result["coverage"]["source_record_counts"] == dict.fromkeys(FILES, 0)


def test_cached_projection_and_responses_are_independent(kit):
    catalog = CostarCatalog(kit)
    first = catalog.search(CostarSearchCriteria())
    expected = copy.deepcopy(first)
    first["results"][0]["asking_rent"]["minimum"] = 1
    first["coverage"]["source_file_sha256"].clear()
    first["limitations"].clear()
    (kit / FILES[0]).write_text("broken", encoding="utf-8")
    assert catalog.search({}) == expected
    assert CostarCatalog(kit).search({})["status"] == "unavailable"


def test_snapshot_hash_binds_source_and_declared_date(kit):
    unknown = CostarCatalog(kit).search({})
    declared = CostarCatalog(kit, snapshot_date="2026-11-12").search({})
    assert unknown["snapshot_date_basis"] == "unknown"
    assert unknown["snapshot_date"] is None
    assert unknown["source_hash"] != declared["source_hash"]
    listing = listing_row()
    listing["apartmentModels"][0]["minRent"] = 1400
    write_kit(kit, listings=[listing])
    assert CostarCatalog(kit).search({})["source_hash"] != unknown["source_hash"]


def test_display_limit_retains_full_match_count_and_stable_sort(tmp_path):
    listings = [listing_row(102, listing_id=502), listing_row()]
    listings[0]["apartmentModels"][0]["minRent"] = 1000
    listings[0]["apartmentModels"][0]["maxRent"] = 2000
    catalog = CostarCatalog(
        write_kit(
            tmp_path,
            listings=listings,
            properties=[property_row(102), property_row()],
            catalog=[(101, "scan-one"), (102, "scan-one")],
        )
    )
    result = catalog.search({"limit": 1})
    assert result["results"][0]["property_id"] == 101
    assert result["coverage"]["matched_floor_plan_count"] == 2
    assert result["coverage"]["returned_count"] == 1
    assert result["coverage"]["has_more"] is True
    assert len(catalog.search({"limit": 2})["results"]) == 2


def test_concurrent_searches_load_once_and_return_independent_results(kit, monkeypatch):
    catalog = CostarCatalog(kit)
    original = catalog._load
    load_calls = []
    start = threading.Barrier(4)

    def load():
        load_calls.append(True)
        return original()

    def search(_):
        start.wait(timeout=5)
        return catalog.search({})

    monkeypatch.setattr(catalog, "_load", load)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(search, range(4)))
    assert load_calls == [True]
    assert all(result == results[0] for result in results)
    results[0]["results"][0]["asking_rent"].clear()
    assert all(result["results"][0]["asking_rent"]["minimum"] == 1300 for result in results[1:])


def test_failed_load_does_not_cache_partial_snapshot(kit):
    catalog = CostarCatalog(kit)
    (kit / FILES[0]).write_text("malformed", encoding="utf-8")
    assert catalog.search({})["status"] == "unavailable"
    assert catalog._snapshot is None
    write_kit(kit)
    assert catalog.search({})["status"] == "ok"
