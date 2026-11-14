"""Read-only Matterport Model API queries bound to local CoStar evidence.

Contract: https://developers.matterport.com/api/property-intelligence/dimensions/
Authentication: https://developers.matterport.com/api/
No arbitrary model IDs, GraphQL, endpoints, mutations, or asset downloads.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr

from spending_api.costar import CostarCatalog, CostarSearchCriteria

MODEL_API_URL = "https://api.matterport.com/api/models/graph"
BASIC_QUERY = """query CatalogModelMetadata($modelId: ID!) {
  model(id: $modelId) {
    id state modified
    floors { id }
    rooms { id label floor { id } }
  }
}"""
DIMENSIONS_QUERY = """query CatalogModelDimensions($modelId: ID!) {
  model(id: $modelId) {
    id state modified
    dimensionEstimates(units: metric) { units area areaIndoor }
    floors { id }
    rooms {
      id label floor { id }
      dimensionEstimates(units: metric) { units area areaIndoor width depth height }
    }
  }
}"""
LEGACY_DIMENSIONS_QUERY = """query CatalogModelDimensionsWithLegacy($modelId: ID!) {
  model(id: $modelId) {
    id state modified
    dimensionEstimates(units: metric) { units area areaIndoor }
    legacyDimensions: dimensions { units areaFloor areaFloorIndoor width depth height }
    floors { id }
    rooms {
      id label floor { id }
      dimensionEstimates(units: metric) { units area areaIndoor width depth height }
      legacyDimensions: dimensions { units areaFloor areaFloorIndoor width depth height }
    }
  }
}"""
LIMITATIONS = [
    "The model is selected from exact local property/floor-plan and same-property CSV references.",
    "The API read verifies model identity; it does not establish current unit availability.",
    "Dimension estimates may be absent or zero and require the account's API entitlement.",
    "Width and depth do not prove an unobstructed rectangle, accessible route or furniture fit.",
    "Room boundaries, obstacles, doorways and tour imagery were not inspected.",
    "Legacy dimensions are reported separately with their returned units; "
    "scan areas are not listing area or usable clear floor space.",
]
_MODEL_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_ERROR_CODES = {
    "request.unauthenticated",
    "request.unauthorized",
    "request.invalid",
    "not.found",
    "not.unique",
    "quota.exceeded",
    "error.internal",
    "GRAPHQL_VALIDATION_FAILED",
}


class _InvalidResponse(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidResponse("invalid_response")
        result[key] = value
    return result


def _invalid_constant(_):
    raise _InvalidResponse("invalid_response")


def _id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise _InvalidResponse("invalid_response")
    return value


def _dimensions(value, *, room=False):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("units") != "metric":
        raise _InvalidResponse("invalid_dimension_units")
    fields = {"area": "area_square_meters", "areaIndoor": "indoor_area_square_meters"}
    if room:
        fields.update(width="width_meters", depth="depth_meters", height="height_meters")
    result = {"measurement_kind": "estimate", "units": "metric"}
    for source, target in fields.items():
        amount = value.get(source)
        if amount is not None and (
            type(amount) not in {int, float} or not math.isfinite(amount) or amount < 0
        ):
            raise _InvalidResponse("invalid_dimensions")
        result[target] = amount
    return result


def _legacy_dimensions(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("units") not in {"metric", "imperial"}:
        raise _InvalidResponse("invalid_dimension_units")
    result = {
        "source_field": "dimensions",
        "measurement_kind": "legacy_estimate",
        "units": value["units"],
        "length_unit": "meters" if value["units"] == "metric" else "feet",
        "area_unit": "square_meters" if value["units"] == "metric" else "square_feet",
    }
    for source, target in {
        "areaFloor": "area_floor",
        "areaFloorIndoor": "area_floor_indoor",
        "width": "width",
        "depth": "depth",
        "height": "height",
    }.items():
        amount = value.get(source)
        if amount is not None and (
            type(amount) not in {int, float} or not math.isfinite(amount) or amount < 0
        ):
            raise _InvalidResponse("invalid_dimensions")
        result[target] = amount
    return result


def _summarize(model, model_id, include_dimensions, include_legacy_dimensions):
    if not isinstance(model, dict) or model.get("id") != model_id:
        raise _InvalidResponse("model_identity_mismatch")
    raw_floors, raw_rooms = model.get("floors"), model.get("rooms")
    for collection, limit in ((raw_floors, 20), (raw_rooms, 200)):
        if collection is not None and (not isinstance(collection, list) or len(collection) > limit):
            raise _InvalidResponse("model_collection_limit_or_shape")
    floors = set()
    for floor in raw_floors or []:
        if not isinstance(floor, dict):
            raise _InvalidResponse("invalid_response")
        identifier = _id(floor.get("id"))
        if identifier in floors:
            raise _InvalidResponse("duplicate_floor_identity")
        floors.add(identifier)
    rooms, room_ids = [], set()
    for room in raw_rooms or []:
        if not isinstance(room, dict):
            raise _InvalidResponse("invalid_response")
        identifier = _id(room.get("id"))
        if identifier in room_ids:
            raise _InvalidResponse("duplicate_room_identity")
        room_ids.add(identifier)
        label = room.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > 200):
            raise _InvalidResponse("invalid_response")
        floor = room.get("floor")
        if floor is not None and not isinstance(floor, dict):
            raise _InvalidResponse("invalid_response")
        floor_id = _id(floor.get("id")) if floor is not None else None
        if floor_id is not None and (raw_floors is None or floor_id not in floors):
            raise _InvalidResponse("room_floor_identity_mismatch")
        estimates = (
            _dimensions(room.get("dimensionEstimates"), room=True) if include_dimensions else None
        )
        rooms.append(
            {
                "room_id": identifier,
                "floor_id": floor_id,
                "label": label,
                "dimension_estimates": estimates,
                "legacy_dimensions": _legacy_dimensions(room.get("legacyDimensions"))
                if include_legacy_dimensions
                else None,
                "positive_width_and_depth_available": bool(
                    estimates
                    and (estimates["width_meters"] or 0) > 0
                    and (estimates["depth_meters"] or 0) > 0
                ),
            }
        )
    modified = model.get("modified")
    if modified is not None:
        if not isinstance(modified, str):
            raise _InvalidResponse("invalid_response")
        parsed = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise _InvalidResponse("invalid_response")
        modified = parsed.astimezone(UTC).isoformat()
    estimates = _dimensions(model.get("dimensionEstimates")) if include_dimensions else None
    legacy_model = (
        _legacy_dimensions(model.get("legacyDimensions")) if include_legacy_dimensions else None
    )
    state = model.get("state")
    if state not in {None, "active", "inactive", "processing", "failed", "deleted"}:
        state = "unknown"
    return {
        "model_state": state,
        "model_modified_at": modified,
        "model_dimension_estimates": estimates,
        "legacy_model_dimensions": legacy_model,
        "floor_count": len(floors) if raw_floors is not None else None,
        "room_count": len(rooms) if raw_rooms is not None else None,
        "rooms_with_positive_width_and_depth": sum(
            row["positive_width_and_depth_available"] for row in rooms
        )
        if raw_rooms is not None and include_dimensions
        else None,
        "rooms_with_positive_legacy_area": sum(
            bool(row["legacy_dimensions"] and (row["legacy_dimensions"]["area_floor"] or 0) > 0)
            for row in rooms
        )
        if raw_rooms is not None and include_legacy_dimensions
        else None,
        "rooms": sorted(rooms, key=lambda row: row["room_id"])[:20],
        "rooms_returned": min(len(rooms), 20),
        "rooms_truncated": len(rooms) > 20,
        "field_availability": {
            "floors": raw_floors is not None,
            "rooms": raw_rooms is not None,
            "model_dimension_estimates": estimates is not None,
            "any_room_dimension_estimates": any(
                row["dimension_estimates"] is not None for row in rooms
            ),
            "legacy_model_dimensions": legacy_model is not None,
            "any_room_legacy_dimensions": any(
                row["legacy_dimensions"] is not None for row in rooms
            ),
        },
        "dimensions_requested": include_dimensions,
        "legacy_dimensions_requested": include_legacy_dimensions,
        "geometry_clearance_verified": False,
        "current_unit_availability_verified": False,
    }


class MatterportClient:
    """One fixed query per call; server-bound catalog selects the model.

    Multiple verified references choose the first sorted ID explicitly. No other
    model is inspected, no retries occur, and partial GraphQL data is not accepted.
    Transport injection is for offline tests, never an HTTP request parameter.
    """

    def __init__(
        self,
        catalog: CostarCatalog,
        token_id: str | SecretStr,
        token_secret: str | SecretStr,
        *,
        timeout_seconds: float = 15,
        max_response_bytes: int = 256 * 1024,
        include_legacy_dimensions: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 30:
            raise ValueError("Matterport timeout must be positive and at most 30 seconds")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 1024 * 1024:
            raise ValueError("Matterport response limit must be 1 to 1048576 bytes")
        if type(include_legacy_dimensions) is not bool:
            raise ValueError("include_legacy_dimensions must be boolean")
        self._catalog = catalog
        self._token_id = token_id if isinstance(token_id, SecretStr) else SecretStr(token_id)
        self._token_secret = (
            token_secret if isinstance(token_secret, SecretStr) else SecretStr(token_secret)
        )
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._include_legacy_dimensions = include_legacy_dimensions
        self._transport = transport

    async def inspect_floor_plan(
        self,
        criteria: CostarSearchCriteria | dict,
        property_id: int,
        floor_plan_id: int,
        *,
        include_dimensions: bool = True,
    ) -> dict:
        criteria = CostarSearchCriteria.model_validate(criteria)
        if any(type(value) is not int or value <= 0 for value in (property_id, floor_plan_id)):
            raise ValueError("Property and floor-plan IDs must be positive integers")
        if type(include_dimensions) is not bool:
            raise ValueError("include_dimensions must be boolean")
        base = {
            "source_kind": "matterport_model_api",
            "property_id": property_id,
            "floor_plan_id": floor_plan_id,
            "limitations": list(LIMITATIONS),
        }
        try:
            async with asyncio.timeout(self._timeout):
                return await self._inspect_floor_plan(
                    criteria, property_id, floor_plan_id, include_dimensions, base
                )
        except TimeoutError:
            return {**base, "status": "unavailable", "reason": "deadline_exceeded"}

    async def _inspect_floor_plan(
        self, criteria, property_id, floor_plan_id, include_dimensions, base
    ):
        # Cancellation stops awaiting this read, not an already running catalog
        # worker. That worker is read-only and cannot subsequently call the API.
        if not self._token_id.get_secret_value() or not self._token_secret.get_secret_value():
            return {**base, "status": "unavailable", "reason": "not_configured"}
        search = await asyncio.to_thread(self._catalog.search, criteria)
        if search.get("status") != "ok":
            return {**base, "status": "unavailable", "reason": "catalog_unavailable"}
        matches = [
            row
            for row in search["results"]
            if row.get("property_id") == property_id and row.get("floor_plan_id") == floor_plan_id
        ]
        if len(matches) != 1:
            return {**base, "status": "selection_rejected", "reason": "not_in_catalog_results"}
        metadata = matches[0].get("matterport", {})
        if (
            metadata.get("scope") != "floor_plan"
            or metadata.get("same_property_catalog_checked") is not True
            or metadata.get("shared_across_properties") is not False
            or metadata.get("shared_across_floor_plans") is not False
        ):
            return {**base, "status": "selection_rejected", "reason": "ambiguous_model_reference"}
        model_ids = metadata.get("model_ids")
        count = metadata.get("model_reference_count")
        if (
            not isinstance(model_ids, list)
            or not model_ids
            or any(
                not isinstance(value, str) or not _MODEL_ID.fullmatch(value) for value in model_ids
            )
            or len(model_ids) != len(set(model_ids))
            or type(count) is not int
            or count < len(model_ids)
        ):
            return {**base, "status": "selection_rejected", "reason": "invalid_model_reference"}
        model_id = sorted(model_ids)[0]
        base.update(
            model_id=model_id,
            catalog_source_hash=search["source_hash"],
            verified_reference_count=count,
            models_requested=1,
            models_inspected=0,
            all_referenced_models_inspected=False,
            selected_reference_scope="floor_plan",
            same_property_catalog_checked=True,
        )
        try:
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=False,
                    trust_env=False,
                    auth=httpx.BasicAuth(
                        self._token_id.get_secret_value(), self._token_secret.get_secret_value()
                    ),
                    transport=self._transport,
                ) as client:
                    async with client.stream(
                        "POST",
                        MODEL_API_URL,
                        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                        json={
                            "query": (
                                LEGACY_DIMENSIONS_QUERY
                                if self._include_legacy_dimensions
                                else DIMENSIONS_QUERY
                            )
                            if include_dimensions
                            else BASIC_QUERY,
                            "variables": {"modelId": model_id},
                        },
                    ) as response:
                        if response.status_code != 200:
                            reason = {
                                401: "authentication_failed",
                                403: "access_denied",
                                404: "model_not_found",
                                429: "rate_limited",
                            }.get(response.status_code, "provider_http_error")
                            return {
                                **base,
                                "status": "unavailable",
                                "reason": reason,
                                "provider_http_status": response.status_code,
                            }
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise _InvalidResponse("unsupported_response_encoding")
                        raw = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                            if len(raw) + len(chunk) > self._max_response_bytes:
                                raise _InvalidResponse("response_limit_exceeded")
                            raw.extend(chunk)
            payload = json.loads(
                raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
            )
            if not isinstance(payload, dict):
                raise _InvalidResponse("invalid_response")
            if payload.get("errors"):
                errors = payload["errors"]
                if not isinstance(errors, list) or any(
                    not isinstance(error, dict) for error in errors
                ):
                    raise _InvalidResponse("invalid_response")
                codes = {
                    error.get("extensions", {}).get("code")
                    for error in errors
                    if isinstance(error.get("extensions"), dict)
                    and isinstance(error["extensions"].get("code"), str)
                } & _ERROR_CODES
                if "request.unauthenticated" in codes:
                    reason = "authentication_failed"
                elif "request.unauthorized" in codes:
                    reason = "access_denied"
                elif codes & {"request.invalid", "GRAPHQL_VALIDATION_FAILED"}:
                    reason = "schema_unsupported"
                elif "not.found" in codes:
                    reason = "model_not_found"
                else:
                    reason = "provider_graphql_error"
                return {
                    **base,
                    "status": "unavailable",
                    "reason": reason,
                    "graphql_error_count": len(errors),
                    "graphql_error_codes": sorted(codes),
                }
            data = payload.get("data")
            if not isinstance(data, dict) or data.get("model") is None:
                return {**base, "status": "unavailable", "reason": "model_unavailable"}
            summary = _summarize(
                data["model"],
                model_id,
                include_dimensions,
                include_dimensions and self._include_legacy_dimensions,
            )
            result = {
                **base,
                "status": "ok",
                "retrieved_at": datetime.now(UTC).isoformat(),
                "models_inspected": 1,
                "all_referenced_models_inspected": count == 1,
                **summary,
            }
            # Preserve full validated counts while explicitly bounding displayed details.
            while len(json.dumps(result, ensure_ascii=False).encode()) > 22_000:
                if not result["rooms"]:
                    raise _InvalidResponse("summary_limit_exceeded")
                result["rooms"].pop()
                result["rooms_returned"] = len(result["rooms"])
                result["rooms_truncated"] = True
            return result
        except _InvalidResponse as error:
            reason = str(error)
        except (TimeoutError, httpx.TimeoutException):
            reason = "deadline_exceeded"
        except httpx.HTTPError:
            reason = "provider_connection_error"
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            reason = "invalid_response"
        return {**base, "status": "unavailable", "reason": reason}
