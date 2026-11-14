"""Bounded, read-only apartment search over the supplied local CoStar data kit.

This module reads two fixed files. Paths are server configuration; no remote
services or model assets are accessed.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FILES = (
    "data/apartment-listing.jsonl",
    "data/commercial-property.jsonl",
)
AUDIENCES = {1: "general", 2: "senior", 3: "student", 4: "military", 5: "corporate", 6: "vacation"}
LIMITATIONS = [
    "Local CoStar snapshot covers supplied Houston and Los Angeles properties, "
    "not the full market.",
    "Asking rents are monthly USD per unit; a low endpoint is not a quote for an available unit.",
    "Recorded availability is historical snapshot evidence, never live availability or a booking.",
    "Unknown currency or rent basis, nonpositive rent or availability, "
    "and join conflicts are excluded.",
    "Fees, deposits, concessions, lease terms and eligibility require independent confirmation.",
]


class CostarSearchCriteria(BaseModel):
    """All filters are explicit; no client-provided paths or source overrides."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    state: Literal["TX", "CA"] = "TX"
    city: str | None = Field(default=None, min_length=1, max_length=80)
    bedrooms: int = Field(default=1, ge=0, le=6)
    max_monthly_rent_usd: int = Field(default=1800, ge=1, le=100_000)
    min_square_feet: int = Field(default=0, ge=0, le=10_000)
    limit: int = Field(default=5, ge=1, le=20)
    include_specialized_housing: bool = False

    @field_validator("city")
    @classmethod
    def nonblank_city(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value or any(ord(char) < 32 for char in value):
                raise ValueError("city must be nonblank text without control characters")
        return value


class _CatalogError(ValueError):
    pass


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _object(value: Any) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _CatalogError("invalid_catalog")
    return value


def _rows(value: Any) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise _CatalogError("invalid_catalog")
    return value


def _text(value: Any, limit: int = 200) -> str | None:
    return value[:limit] if isinstance(value, str) and value.strip() else None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _CatalogError("invalid_catalog")
        result[key] = value
    return result


def _no_constant(_: str) -> None:
    raise _CatalogError("invalid_catalog")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class CostarCatalog:
    """Load a bounded projection once; create a new service to load a new snapshot.

    A search examines the complete bounded projection before applying the display
    limit. Oversized or malformed inputs fail explicitly, never as partial results.
    Only the projected data and hashes are retained; contact records are discarded.
    """

    def __init__(
        self,
        data_dir: Path | str | None,
        *,
        snapshot_date: str | None = None,
        max_file_bytes: int = 256 * 1024 * 1024,
        max_records: int = 20_000,
        max_floor_plans: int = 50_000,
        max_record_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        for value in (max_file_bytes, max_records, max_floor_plans, max_record_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("catalog limits must be positive integers")
        if snapshot_date is not None:
            if (
                not isinstance(snapshot_date, str)
                or date.fromisoformat(snapshot_date).isoformat() != snapshot_date
            ):
                raise ValueError("snapshot_date must be an ISO date")
        self._directory = Path(data_dir) if data_dir is not None else None
        self._snapshot_date = snapshot_date
        self._max_file_bytes = max_file_bytes
        self._max_records = max_records
        self._max_floor_plans = max_floor_plans
        self._max_record_bytes = max_record_bytes
        self._snapshot: dict | None = None
        self._lock = threading.Lock()

    def _path(self, relative: str) -> Path:
        assert self._directory is not None
        root = self._directory.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise _CatalogError("invalid_catalog_path")
        if path.stat().st_size > self._max_file_bytes:
            raise _CatalogError("catalog_limit_exceeded")
        return path

    def _json_rows(self, relative: str, hashes: dict, counts: dict):
        digest = hashlib.sha256()
        total = count = 0
        # Larger reads avoid small-I/O latency on Docker Desktop bind mounts.
        with self._path(relative).open("rb", buffering=1024 * 1024) as stream:
            line_number = 0
            while raw := stream.readline(self._max_record_bytes + 1):
                line_number += 1
                total += len(raw)
                if len(raw) > self._max_record_bytes or total > self._max_file_bytes:
                    raise _CatalogError("catalog_limit_exceeded")
                digest.update(raw)
                if not raw.strip():
                    continue
                count += 1
                if count > self._max_records:
                    raise _CatalogError("catalog_limit_exceeded")
                row = json.loads(
                    raw.decode("utf-8-sig"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_no_constant,
                )
                if not isinstance(row, dict):
                    raise _CatalogError("invalid_catalog")
                yield line_number, row
        hashes[relative], counts[relative] = digest.hexdigest(), count

    def _load(self) -> dict:
        hashes, counts = {}, {}
        properties = {}
        plan_count = 0
        for line, row in self._json_rows(FILES[1], hashes, counts):
            property_id = row.get("id")
            if not _positive(property_id):
                raise _CatalogError("invalid_catalog")
            address = _object(row.get("primaryAddress"))
            models = set()
            for model in _rows(_object(row.get("propertyApartment")).get("models")):
                plan_count += 1
                if plan_count > self._max_floor_plans:
                    raise _CatalogError("catalog_limit_exceeded")
                model_id = model.get("id")
                if not _positive(model_id):
                    continue
                models.add(model_id)
            projected = {
                "state": _text(address.get("state")),
                "city": _text(address.get("city")),
                "models": models,
            }
            if property_id in properties and properties[property_id][0] != projected:
                raise _CatalogError("conflicting_property_records")
            properties.setdefault(property_id, (projected, line))

        candidates, duplicate_fingerprints, listing_identities = {}, defaultdict(set), {}
        listing_plan_count = 0
        for line, listing in self._json_rows(FILES[0], hashes, counts):
            listing_id, property_id = listing.get("id"), listing.get("propertyId")
            if not _positive(listing_id) or not _positive(property_id):
                raise _CatalogError("invalid_catalog")
            address = _object(listing.get("address"))
            plans = []
            for plan in _rows(listing.get("apartmentModels")):
                listing_plan_count += 1
                if listing_plan_count > self._max_floor_plans:
                    raise _CatalogError("catalog_limit_exceeded")
                if not _positive(plan.get("id")):
                    raise _CatalogError("invalid_catalog")
                plans.append(
                    {
                        key: plan.get(key)
                        for key in (
                            "id",
                            "name",
                            "numBeds",
                            "minRent",
                            "maxRent",
                            "monetaryUnit",
                            "rentPricingType",
                            "numUnitsAvailable",
                            "minSquareFeet",
                        )
                    }
                )
            base = {
                "listing_id": listing_id,
                "property_id": property_id,
                "name": _text(listing.get("buildingName")),
                "listing_state": _text(address.get("state")),
                "lifestyle": listing.get("lifestyleType"),
            }
            listing_fingerprint = _canonical([base, sorted(plans, key=_canonical)])
            if (
                listing_id in listing_identities
                and listing_identities[listing_id] != listing_fingerprint
            ):
                raise _CatalogError("conflicting_listing_records")
            listing_identities[listing_id] = listing_fingerprint
            for plan in plans:
                key = (property_id, plan["id"])
                fingerprint = _canonical([base, plan])
                duplicate_fingerprints[key].add(fingerprint)
                candidates.setdefault(key, (base, plan, line))
        conflicts = {key for key, values in duplicate_fingerprints.items() if len(values) > 1}
        return {
            "hash": hashlib.sha256(_canonical([hashes, self._snapshot_date]).encode()).hexdigest(),
            "hashes": hashes,
            "counts": counts,
            "properties": properties,
            "candidates": candidates,
            "conflicts": conflicts,
            "listing_plan_count": listing_plan_count,
        }

    def _candidate(
        self, key: tuple[int, int], candidate: tuple, criteria: CostarSearchCriteria, snapshot: dict
    ):
        base, plan, line = candidate
        if key in snapshot["conflicts"]:
            return None, "conflicting_floor_plan_listings"
        prop_entry = snapshot["properties"].get(key[0])
        if prop_entry is None:
            return None, "property_join_missing"
        prop, property_line = prop_entry
        if base["listing_state"] and prop["state"] and base["listing_state"] != prop["state"]:
            return None, "conflicting_location"
        if prop["state"] != criteria.state:
            return None, "outside_requested_state"
        if criteria.city and (prop["city"] or "").casefold() != criteria.city.casefold():
            return None, "outside_requested_city"
        lifestyle = base["lifestyle"]
        if type(lifestyle) is not int or lifestyle not in AUDIENCES:
            return None, "housing_audience_unknown"
        if lifestyle != 1 and not criteria.include_specialized_housing:
            return None, "specialized_housing_excluded"
        if type(plan["numBeds"]) is not int or plan["numBeds"] != criteria.bedrooms:
            return None, "bedrooms_do_not_match"
        if type(plan["monetaryUnit"]) is not int or plan["monetaryUnit"] != 1:
            return None, "currency_unknown_or_not_usd"
        if type(plan["rentPricingType"]) is not int or plan["rentPricingType"] != 1:
            return None, "rent_not_per_unit"
        low, high = plan["minRent"], plan["maxRent"]
        if not _positive(low):
            return None, "asking_rent_missing"
        high = high if _positive(high) else None
        if high is not None and high < low:
            return None, "inconsistent_rent_range"
        if low > criteria.max_monthly_rent_usd:
            return None, "minimum_rent_above_budget"
        if not _positive(plan["numUnitsAvailable"]):
            return None, "no_positive_recorded_availability"
        area = plan["minSquareFeet"] if _positive(plan["minSquareFeet"]) else None
        if criteria.min_square_feet and (area is None or area < criteria.min_square_feet):
            return None, "area_unknown_or_too_small"
        if key[1] not in prop["models"]:
            return None, "floor_plan_join_missing"
        return {
            "property_id": key[0],
            "floor_plan_id": key[1],
            "listing_id": base["listing_id"],
            "property_name": base["name"],
            "floor_plan_name": _text(plan["name"]),
            "city": prop["city"],
            "state": prop["state"],
            "bedrooms": plan["numBeds"],
            "housing_audience": AUDIENCES[lifestyle],
            "minimum_square_feet": area,
            "asking_rent": {
                "minimum": low,
                "maximum": high,
                "currency": "USD",
                "period": "month",
                "basis": "per_unit",
            },
            "price_range_within_budget": high is not None and high <= criteria.max_monthly_rent_usd,
            "recorded_units_available": plan["numUnitsAvailable"],
            "availability_is_live": False,
            "evidence": [
                {
                    "file": FILES[0],
                    "line": line,
                    "listing_id": base["listing_id"],
                    "floor_plan_id": key[1],
                },
                {
                    "file": FILES[1],
                    "line": property_line,
                    "property_id": key[0],
                    "floor_plan_id": key[1],
                },
            ],
        }, None

    def search(self, criteria: CostarSearchCriteria | dict) -> dict:
        criteria = CostarSearchCriteria.model_validate(criteria)
        base = {
            "source_kind": "costar_local_snapshot",
            "snapshot_date": self._snapshot_date,
            "snapshot_date_basis": "server_declared" if self._snapshot_date else "unknown",
            "criteria": criteria.model_dump(),
            "limitations": list(LIMITATIONS),
            "schema_basis": {
                "documents": ["index.md", "models/apartment-listing.md"],
                "currency": "monetaryUnit 1 = USD; unknown values are excluded",
                "rent_basis": "rentPricingType 1 = monthly per unit",
                "floor_plan_join": "listing.propertyId + apartmentModels.id",
            },
        }
        if self._directory is None:
            return {
                **base,
                "status": "unavailable",
                "reason": "not_configured",
                "results": [],
                "coverage": {},
            }
        try:
            with self._lock:
                if self._snapshot is None:
                    self._snapshot = self._load()
                snapshot = self._snapshot
            matches, excluded = [], Counter()
            for key, candidate in snapshot["candidates"].items():
                result, reason = self._candidate(key, candidate, criteria, snapshot)
                if result is not None:
                    matches.append(result)
                else:
                    excluded[reason] += 1
            matches.sort(
                key=lambda row: (
                    not row["price_range_within_budget"],
                    row["asking_rent"]["minimum"],
                    row["property_id"],
                    row["floor_plan_id"],
                )
            )
            return {
                **base,
                "status": "ok",
                "source_hash": snapshot["hash"],
                "results": matches[: criteria.limit],
                "coverage": {
                    "source_record_counts": dict(snapshot["counts"]),
                    "source_file_sha256": dict(snapshot["hashes"]),
                    "raw_listing_floor_plan_count": snapshot["listing_plan_count"],
                    "unique_property_floor_plan_count": len(snapshot["candidates"]),
                    "matched_floor_plan_count": len(matches),
                    "returned_count": min(len(matches), criteria.limit),
                    "has_more": len(matches) > criteria.limit,
                    "exclusions": dict(sorted(excluded.items())),
                    "market_coverage_complete": False,
                    "current_availability_verified": False,
                },
            }
        except FileNotFoundError:
            reason = "catalog_files_missing"
        except _CatalogError as error:
            reason = str(error)
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
            reason = "invalid_catalog"
        return {**base, "status": "unavailable", "reason": reason, "results": [], "coverage": {}}
