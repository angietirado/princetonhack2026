"""Build a fixed shopping world around three recorded Houston rentals.

CoStar supplies public historical rental facts. The store world, prices, budgets,
and travel assumptions are simulation inputs; housing values never imply income.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path

WORLD_ID = "houston-inner-loop"
# Explicit public rental records, not nearest-neighbor substitutions.
HOMES = [
    ("houston-rental-west", "West · Rosine Street", 240486874),
    ("houston-rental-central", "Central · Ruthven Street", 232112148),
    ("houston-rental-east", "East · Polk Street", 238679692),
]
DEFAULTS = {
    "budget_cents": 6500,
    "time_budget_minutes": 45,
    "travel_mode": "drive",
    "max_stores": 2,
}
BASKET = [
    ("milk", "Milk", 1, "1 gallon", "Dairy", 399),
    ("eggs", "Eggs", 1, "dozen", "Dairy", 329),
    ("bread", "Whole-wheat bread", 2, "loaf", "Bakery", 299),
    ("rice", "Rice", 1, "2 lb bag", "Pantry", 379),
    ("chicken", "Chicken", 2, "1 lb", "Protein", 649),
    ("apples", "Apples", 2, "1 lb", "Produce", 249),
    ("greens", "Salad greens", 1, "bag", "Produce", 329),
    ("coffee", "Ground coffee", 1, "12 oz bag", "Pantry", 899),
]
# Fixed latitude/longitude, shopping minutes, and integer price percentages.
# These fictional locations stay put when the starting home changes.
STORES = [
    ("corner", "Corner Market", 29.7576, -95.3976, 7, [130,130,130,130,130,130,130,130]),
    ("grove", "Grove Grocer", 29.7565, -95.3810, 10, [105,104,110,105,108,82,80,110]),
    ("pantry", "Pantry & Co.", 29.7520, -95.3580, 11, [95,108,80,74,112,110,110,75]),
    ("value", "Value Foods", 29.7448, -95.3748, 16, [82,82,83,84,82,86,86,88]),
    ("fresh", "Fresh Stop", 29.7492, -95.3532, 12, [108,90,100,110,70,70,70,105]),
]
TRAVEL = {
    "method": "modeled_distance",
    "road_factor": 1.3,
    "walk_kmh": 4.5,
    "drive_kmh": 24,
    "drive_cost_cents_per_km": 25,
    "drive_parking_minutes_per_stop": 3,
}


def canonical_hash(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _number(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} is outside the supported range")
    return value


def _identifier(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return str(value)


def _date(value, label):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a recorded ISO date")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be a recorded ISO date") from error
    return value


def rental_home(row, label):
    record_id = _identifier(row.get("id"), "Listing id")
    permissions = _object(row.get("permissionLevels"), "Listing permissions")
    if type(permissions.get("homes")) is not int or permissions["homes"] != 10:
        raise ValueError(f"Listing {record_id} is not public-display data")
    if type(row.get("transactionType")) is not int or row["transactionType"] != 2:
        raise ValueError(f"Listing {record_id} is not a rental")
    if type(row.get("listingStatus")) is not int or row["listingStatus"] not in (1, 2):
        raise ValueError(f"Listing {record_id} was not active or under contract in the source")
    if type(row.get("monetaryUnit")) is not int or row["monetaryUnit"] != 1:
        raise ValueError(f"Listing {record_id} does not have a known USD rent")
    prop = _object(row.get("property"), "Property")
    address = _object(prop.get("address"), "Property address")
    if address.get("city") != "Houston" or address.get("state") != "TX":
        raise ValueError(f"Listing {record_id} is outside Houston, Texas")
    latitude = _number(address.get("latitude"), "Latitude", -90, 90)
    longitude = _number(address.get("longitude"), "Longitude", -180, 180)
    rent = row.get("currentPrice")
    if isinstance(rent, bool) or not isinstance(rent, (int, float, Decimal)):
        raise ValueError(f"Listing {record_id} has no numeric monthly asking rent")
    rent = Decimal(str(rent))
    cents = rent * 100
    if not rent.is_finite() or rent <= 0 or cents != cents.to_integral_value():
        raise ValueError(f"Listing {record_id} has an invalid monthly asking rent")
    address_text = address.get("fullStreetAddress")
    if not isinstance(address_text, str) or not address_text.strip():
        raise ValueError(f"Listing {record_id} has no public street address")
    beds_min = prop.get("bedsMin")
    beds_max = prop.get("bedsMax")
    if (type(beds_min) is not int or type(beds_max) is not int
            or not 0 <= beds_min <= beds_max <= 20):
        raise ValueError(f"Listing {record_id} has an invalid bedroom range")
    sqft_min = _number(prop.get("sqftMin"), "Living area minimum", 1, 100_000)
    sqft_max = _number(prop.get("sqftMax"), "Living area maximum", sqft_min, 100_000)
    unit = address.get("unitNumber")
    if unit is not None and not isinstance(unit, str):
        raise ValueError(f"Listing {record_id} has an invalid unit label")
    home = {
        "label": f"Your {label.split(' · ')[0].lower()} starting point",
        "latitude": latitude,
        "longitude": longitude,
        "source": "costar",
        "source_record_id": record_id,
        "source_dataset": "residential-listings",
        "source_property_id": _identifier(row.get("propertyId"), "Property id"),
        "source_residential_listing_id": _identifier(
            row.get("residentialListingId"), "Residential listing id"
        ),
        "source_permission_homes": 10,
        "source_transaction_type": 2,
        "source_listing_status": row["listingStatus"],
        "source_monetary_unit": 1,
        "source_listing_date": _date(row.get("listDate"), "Listing date"),
        "source_dom_date": _date(row.get("domDate"), "Days-on-market date"),
        "source_snapshot_date": None,
        "current_availability_verified": False,
        "recorded_address": address_text.strip(),
        "unit_number": unit,
        "bedrooms_min": beds_min,
        "bedrooms_max": beds_max,
        "living_area_sqft_min": sqft_min,
        "living_area_sqft_max": sqft_max,
        "monthly_rent_cents": int(cents),
        "housing_price_usd": float(rent),
        "housing_price_basis": "monthly_rent",
        "additional_monthly_fees_cents": None,
        "additional_monthly_fees_status": "unknown",
        "all_in_monthly_housing_cost_cents": None,
    }
    # Fingerprint the exact public factual projection used, not unrelated source contacts.
    home["source_fingerprint"] = canonical_hash(home)
    unit_text = f", unit {unit}" if unit else ""
    home["housing_context"] = (
        f"Recorded asking rent: ${rent:,.2f}/month at {address_text}{unit_text}. "
        f"{beds_min if beds_min == beds_max else str(beds_min) + '-' + str(beds_max)} bedrooms; "
        f"{sqft_min:,.0f} sq ft"
        f"{'' if sqft_min == sqft_max else '-' + format(sqft_max, ',.0f')}. "
        "Historical listing context; current availability and additional fees are unverified. "
        "No resident income or shopping allowance is inferred."
    )
    return home


def shopping_world():
    basket = [
        {"id": key, "name": name, "quantity": quantity, "unit": unit, "category": category}
        for key, name, quantity, unit, category, _price in BASKET
    ]
    stores = []
    for key, name, latitude, longitude, shopping, percentages in STORES:
        stores.append({
            "id": f"{WORLD_ID}-{key}",
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "shopping_minutes": shopping,
            # Integer half-up pricing avoids binary floating point or banker's rounding.
            "prices": {
                item[0]: (item[5] * percent + 50) // 100
                for item, percent in zip(BASKET, percentages, strict=True)
            },
        })
    world = {"world_id": WORLD_ID, "basket": basket, "stores": stores, "travel": TRAVEL}
    return world, canonical_hash(world)


def build(source: Path):
    wanted = {record_id for _scenario_id, _label, record_id in HOMES}
    records = {}
    with source.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line, parse_float=Decimal)
            if not isinstance(row, dict):
                raise ValueError(f"Source line {line_number} is not a record")
            record_id = row.get("id")
            if type(record_id) is int and record_id in wanted:
                if record_id in records:
                    raise ValueError(f"Duplicate selected listing {record_id}")
                records[record_id] = row
    missing = wanted - records.keys()
    if missing:
        raise ValueError("Missing selected rental listings: " + ", ".join(map(str, sorted(missing))))
    world, world_version = shopping_world()
    scenarios = []
    for scenario_id, label, record_id in HOMES:
        home = rental_home(records[record_id], label)
        scenario = {
            "id": scenario_id,
            "name": f"{label} shopping challenge",
            "description": (
                "Shop the same eight-item basket at the same five fictional stores. "
                "Only your recorded rental starting point changes."
            ),
            "world_id": WORLD_ID,
            "world_version": world_version,
            "home": home,
            "defaults": deepcopy(DEFAULTS),
            "basket": deepcopy(world["basket"]),
            "stores": deepcopy(world["stores"]),
            "travel": deepcopy(world["travel"]),
            "provenance": {
                "housing": (
                    "Coordinates, monthly asking rent, bedrooms and living area come from an "
                    "explicit public-display CoStar rental record. The source recorded an active "
                    "or under-contract listing; current availability is not verified. The homes "
                    "differ in size and features, so this is not a like-for-like housing valuation."
                ),
                "prices": (
                    "All store prices are simulated, not live or verified quotes. Prices include "
                    "any modeled tax; no discounts or availability variation."
                ),
                "travel": (
                    "Distance-based travel, parking and shopping estimates, including the return "
                    "home; not routing directions, live traffic, pedestrian access or transit."
                ),
                "stores": (
                    "All five stores are fictional, fixed locations in one shared Houston world. "
                    "Their coordinates, IDs and prices do not change when the home changes."
                ),
                "budget": (
                    "The same hypothetical shopping allowance and time budget apply to every "
                    "starting home; neither is inferred from rent or property value."
                ),
                "housing_fees": (
                    "Additional housing fees and utilities are unknown, not zero. Recorded rent "
                    "is not an all-in monthly housing cost."
                ),
            },
        }
        scenario["version"] = canonical_hash(scenario)
        scenarios.append(scenario)
    return {"schema_version": 1, "scenarios": scenarios}


def write_catalog(payload, output: Path):
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix=f".{output.name}.",
            suffix=".tmp", dir=output.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("costardata/data/residential-listings.jsonl")
    )
    parser.add_argument("--output", type=Path, default=Path("data/scenarios.json"))
    args = parser.parse_args(argv)
    try:
        payload = build(args.source)
        write_catalog(payload, args.output)
    except (OSError, ValueError, TypeError) as error:
        parser.error(f"Cannot rebuild scenario catalog: {error}")
    print(f"Wrote {len(payload['scenarios'])} rental scenarios in one fixed world to {args.output}")


if __name__ == "__main__":
    main()
