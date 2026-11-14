"""Build a small, reproducible simulation from public Houston listing locations.

Never derives income or spending power from housing prices. Store identities,
locations, prices, travel speeds, and participant budgets are simulation inputs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

CENTERS = [
    ("houston-heights", "Heights", 29.796, -95.400, 6500, 45, "drive"),
    ("houston-montrose", "Montrose", 29.746, -95.400, 6000, 50, "walk"),
    ("houston-eastwood", "Eastwood", 29.737, -95.327, 5500, 35, "drive"),
]
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
# East/north offsets in km; each intentionally fictional store offers a tradeoff.
STORES = [
    ("corner", "Corner Market", .16, .08, 7, [1.30,1.30,1.30,1.30,1.30,1.30,1.30,1.30]),
    ("grove", "Grove Grocer", -.48, .30, 10, [1.05,1.04,1.10,1.05,1.08,.82,.80,1.10]),
    ("pantry", "Pantry & Co.", .65, -.38, 11, [.95,1.08,.80,.74,1.12,1.10,1.10,.75]),
    ("value", "Value Foods", -1.30, -.70, 16, [.82,.82,.83,.84,.82,.86,.86,.88]),
    ("fresh", "Fresh Stop", 1.65, .85, 12, [1.08,.90,1.00,1.10,.70,.70,.70,1.05]),
]
def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2-p1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def build(source: Path):
    candidates = []
    with source.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            address = row.get("property", {}).get("address", {})
            if (address.get("city") == "Houston" and address.get("state") == "TX"
                and row.get("permissionLevels", {}).get("homes") == 10
                and isinstance(address.get("latitude"), (int, float))
                and isinstance(address.get("longitude"), (int, float))):
                candidates.append(row)
    if not candidates:
        raise ValueError("No public Houston locations in supplied catalog.")
    scenarios = []
    for sid, name, lat, lon, budget, minutes, mode in CENTERS:
        row = min(candidates, key=lambda r: (
            distance_km(lat,lon,r["property"]["address"]["latitude"],r["property"]["address"]["longitude"]),
            r["id"]))
        address = row["property"]["address"]
        lat, lon = address["latitude"], address["longitude"]
        rental = row.get("transactionType") == 2
        asking = row.get("currentPrice") if row.get("listingStatus") in [1,2] else None
        context = "Recorded Houston home location. No resident income or spending is inferred."
        if asking is not None:
            kind = "monthly asking rent" if rental else "asking sale price"
            context = f"Recorded {kind}: ${asking:,.0f}. Historical listing context; no resident income is inferred."
        scenario = {
            "id": sid, "version": "", "name": f"{name} shopping challenge",
            "description": f"Complete the same eight-item basket from a home near {name}. Decide what convenience is worth to you.",
            "home": {"label": f"Your {name} starting point", "latitude": lat, "longitude": lon,
                "housing_context": context, "source": "costar", "source_record_id": str(row["id"]),
                "source_dataset": "residential-listings", "recorded_address": address.get("fullStreetAddress"),
                "housing_price_usd": asking, "housing_price_basis": "monthly_rent" if rental else "sale_asking"},
            "defaults": {"budget_cents":budget, "time_budget_minutes":minutes, "travel_mode":mode, "max_stores":2},
            "basket": [{"id":i,"name":n,"quantity":q,"unit":u,"category":c} for i,n,q,u,c,p in BASKET],
            "stores": [],
            "travel": {"method":"modeled_distance","road_factor":1.3,"walk_kmh":4.5,"drive_kmh":24,
                "drive_cost_cents_per_km":25,"drive_parking_minutes_per_stop":3},
            "provenance": {
                "housing":"Home coordinates come from a public-display CoStar record in the supplied historical Houston kit. Not household financial data.",
                "prices":"All store prices are simulated, not live or verified quotes. Prices include any modeled tax; no discounts or availability variation.",
                "travel":"Distance-based travel and shopping time estimates; not routing directions, live traffic, or transit service.",
                "stores":"All five stores are fictional simulation locations around the selected home.",
                "budget":"Assigned shopping allowance, not an income estimate from property value.",
            },
        }
        for key, store_name, east, north, shopping, multipliers in STORES:
            scenario["stores"].append({"id":f"{sid}-{key}","name":store_name,
                "latitude":round(lat+north/111.32,6), "longitude":round(lon+east/(111.32*math.cos(math.radians(lat))),6),
                "shopping_minutes":shopping,
                "prices":{item[0]:int(round(item[5]*factor)) for item,factor in zip(BASKET,multipliers)}})
        scenario["version"] = hashlib.sha256(json.dumps(scenario,sort_keys=True).encode()).hexdigest()[:12]
        scenarios.append(scenario)
    return {"schema_version":1,"scenarios":scenarios}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("costardata/data/residential-listings.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/scenarios.json"))
    args = parser.parse_args()
    payload = build(args.source)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    print(f"Wrote {len(payload['scenarios'])} versioned simulation scenarios to {args.output}")
if __name__ == "__main__":
    main()
