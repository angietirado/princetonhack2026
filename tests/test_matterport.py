"""Offline transport and catalog-bound Model API tests. No credentials required."""

import asyncio
import copy
import json
import threading
import time

import httpx
import pytest
from pydantic import ValidationError

from spending_api.matterport import (
    BASIC_QUERY,
    DIMENSIONS_QUERY,
    LEGACY_DIMENSIONS_QUERY,
    MODEL_API_URL,
    MatterportClient,
)


def catalog_result():
    return {
        "status": "ok",
        "source_hash": "a" * 64,
        "results": [
            {
                "property_id": 101,
                "floor_plan_id": 7,
                "matterport": {
                    "scope": "floor_plan",
                    "same_property_catalog_checked": True,
                    "shared_across_properties": False,
                    "shared_across_floor_plans": False,
                    "model_ids": ["synthetic-model"],
                    "model_reference_count": 1,
                    "model_references_truncated": False,
                },
            }
        ],
    }


class Catalog:
    def __init__(self, result=None):
        self.result = catalog_result() if result is None else result
        self.calls = []

    def search(self, criteria):
        self.calls.append(criteria.model_dump())
        return copy.deepcopy(self.result)


def model_response():
    return {
        "data": {
            "model": {
                "id": "synthetic-model",
                "state": "active",
                "modified": "2026-11-01T15:00:00Z",
                "dimensionEstimates": {"units": "metric", "area": 60.0, "areaIndoor": 55.0},
                "floors": [{"id": "floor-1"}],
                "rooms": [
                    {
                        "id": "room-1",
                        "floor": {"id": "floor-1"},
                        "label": "Synthetic Room",
                        "dimensionEstimates": {
                            "units": "metric",
                            "area": 12.5,
                            "areaIndoor": 12.5,
                            "width": 4.5,
                            "depth": 3.0,
                            "height": 2.7,
                        },
                    }
                ],
            }
        }
    }


def inspect(response=None, *, catalog=None, include_dimensions=True, **options):
    calls = []

    def handler(request):
        calls.append(request)
        return (
            response
            if isinstance(response, httpx.Response)
            else httpx.Response(
                200,
                json=model_response() if response is None else response,
            )
        )

    client = MatterportClient(
        catalog or Catalog(),
        "synthetic-id",
        "synthetic-secret",
        transport=httpx.MockTransport(handler),
        **options,
    )
    result = asyncio.run(
        client.inspect_floor_plan({}, 101, 7, include_dimensions=include_dimensions)
    )
    return result, calls


def test_fixed_query_exact_catalog_binding_and_metric_estimates():
    catalog = Catalog()
    result, calls = inspect(catalog=catalog)
    assert len(catalog.calls) == len(calls) == 1
    assert str(calls[0].url) == MODEL_API_URL
    assert calls[0].method == "POST"
    assert calls[0].headers["authorization"].startswith("Basic ")
    assert calls[0].headers["accept-encoding"] == "identity"
    assert json.loads(calls[0].content) == {
        "query": DIMENSIONS_QUERY,
        "variables": {"modelId": "synthetic-model"},
    }
    assert "mutation" not in DIMENSIONS_QUERY.lower()
    assert "description" not in DIMENSIONS_QUERY.lower()
    assert result["status"] == "ok"
    assert result["source_kind"] == "matterport_model_api"
    assert result["catalog_source_hash"] == "a" * 64
    assert result["model_id"] == "synthetic-model"
    assert result["property_id"] == 101
    assert result["floor_plan_id"] == 7
    assert result["room_count"] == result["floor_count"] == 1
    assert result["rooms_with_positive_width_and_depth"] == 1
    assert result["model_dimension_estimates"]["area_square_meters"] == 60
    assert result["rooms"][0]["dimension_estimates"]["width_meters"] == 4.5
    assert result["rooms"][0]["dimension_estimates"]["measurement_kind"] == "estimate"
    assert result["model_modified_at"] == "2026-11-01T15:00:00+00:00"
    assert result["geometry_clearance_verified"] is False
    assert result["current_unit_availability_verified"] is False
    assert result["models_inspected"] == 1
    assert result["all_referenced_models_inspected"] is True
    assert "synthetic-secret" not in json.dumps(result)


def test_basic_query_does_not_assert_dimension_availability():
    result, calls = inspect(include_dimensions=False)
    assert json.loads(calls[0].content)["query"] == BASIC_QUERY
    assert "dimensionEstimates" not in BASIC_QUERY
    assert result["status"] == "ok"
    assert result["dimensions_requested"] is False
    assert result["model_dimension_estimates"] is None
    assert result["rooms"][0]["dimension_estimates"] is None
    assert result["rooms_with_positive_width_and_depth"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scope", "example_unit"),
        ("scope", "community_only"),
        ("scope", "no_scan"),
        ("same_property_catalog_checked", False),
        ("same_property_catalog_checked", 1),
        ("shared_across_properties", True),
        ("shared_across_properties", None),
        ("shared_across_floor_plans", True),
        ("shared_across_floor_plans", 0),
    ],
)
def test_ambiguous_reference_is_rejected_before_network(field, value):
    source = catalog_result()
    source["results"][0]["matterport"][field] = value
    result, calls = inspect(catalog=Catalog(source))
    assert calls == []
    assert result["status"] == "selection_rejected"
    assert result["reason"] == "ambiguous_model_reference"


@pytest.mark.parametrize("change", ["property", "plan", "empty", "duplicate", "unavailable"])
def test_selection_must_be_unique_in_current_server_bound_results(change):
    source = catalog_result()
    if change == "property":
        source["results"][0]["property_id"] = 102
    elif change == "plan":
        source["results"][0]["floor_plan_id"] = 8
    elif change == "empty":
        source["results"] = []
    elif change == "duplicate":
        source["results"] *= 2
    else:
        source["status"] = "unavailable"
    result, calls = inspect(catalog=Catalog(source))
    assert calls == []
    assert result["status"] != "ok"


@pytest.mark.parametrize(
    "model_ids",
    [[], None, "synthetic-model", [True], ["a/a"], ["a b"], [""], ["x" * 101], ["a", "a"]],
)
def test_invalid_model_reference_does_not_become_a_query(model_ids):
    source = catalog_result()
    source["results"][0]["matterport"]["model_ids"] = model_ids
    result, calls = inspect(catalog=Catalog(source))
    assert result["reason"] == "invalid_model_reference"
    assert calls == []


def test_one_of_multiple_references_is_explicitly_selected():
    source = catalog_result()
    source["results"][0]["matterport"].update(
        model_ids=["zzz-other-model", "synthetic-model"],
        model_reference_count=2,
    )
    result, calls = inspect(catalog=Catalog(source))
    assert len(calls) == 1
    assert result["status"] == "ok"
    assert result["model_id"] == "synthetic-model"
    assert result["verified_reference_count"] == 2
    assert result["models_inspected"] == 1
    assert result["all_referenced_models_inspected"] is False


@pytest.mark.parametrize(
    "criteria", [{"data_dir": "other"}, {"model_id": "other"}, {"limit": 21}, {"bedrooms": True}]
)
def test_criteria_revalidated_before_catalog_or_provider(criteria):
    catalog = Catalog()
    client = MatterportClient(catalog, "id", "secret")
    with pytest.raises(ValidationError):
        asyncio.run(client.inspect_floor_plan(criteria, 101, 7))
    assert catalog.calls == []


@pytest.mark.parametrize("property_id", [True, "101", None, 0, -1, 1.5])
def test_strict_property_identity(property_id):
    catalog = Catalog()
    client = MatterportClient(catalog, "id", "secret")
    with pytest.raises(ValueError):
        asyncio.run(client.inspect_floor_plan({}, property_id, 7))
    assert catalog.calls == []


def test_missing_credentials_do_not_read_catalog_or_provider():
    catalog = Catalog()
    result = asyncio.run(MatterportClient(catalog, "", "").inspect_floor_plan({}, 101, 7))
    assert result["reason"] == "not_configured"
    assert catalog.calls == []


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "authentication_failed"),
        (403, "access_denied"),
        (404, "model_not_found"),
        (429, "rate_limited"),
        (500, "provider_http_error"),
        (302, "provider_http_error"),
    ],
)
def test_http_errors_are_sanitized_and_redirects_not_followed(status, reason):
    response = httpx.Response(
        status,
        text="private-provider-error-and-secret",
        headers={"Location": "https://untrusted.invalid"},
    )
    result, calls = inspect(response)
    assert len(calls) == 1
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    assert result["provider_http_status"] == status
    assert result["models_inspected"] == 0
    assert result["all_referenced_models_inspected"] is False
    assert "private-provider" not in json.dumps(result)
    assert "untrusted" not in json.dumps(result)


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("request.unauthenticated", "authentication_failed"),
        ("request.unauthorized", "access_denied"),
        ("request.invalid", "schema_unsupported"),
        ("GRAPHQL_VALIDATION_FAILED", "schema_unsupported"),
        ("not.found", "model_not_found"),
        ("private-custom-code", "provider_graphql_error"),
    ],
)
def test_graphql_errors_discard_partial_data_and_never_echo_messages(code, reason):
    response = model_response()
    response["errors"] = [{"message": "private-message-and-secret", "extensions": {"code": code}}]
    result, calls = inspect(response)
    assert len(calls) == 1
    assert result["reason"] == reason
    assert result["status"] == "unavailable"
    assert "rooms" not in result
    assert "private-" not in json.dumps(result)
    assert result["models_inspected"] == 0


@pytest.mark.parametrize("value", [None, 0, 0.0])
def test_missing_or_zero_room_dimensions_never_establish_clearance(value):
    response = model_response()
    dims = response["data"]["model"]["rooms"][0]["dimensionEstimates"]
    dims.update(width=value, depth=value)
    result, _ = inspect(response)
    assert result["status"] == "ok"
    assert result["rooms"][0]["dimension_estimates"]["width_meters"] == value
    assert result["rooms_with_positive_width_and_depth"] == 0
    assert result["geometry_clearance_verified"] is False


def test_null_collections_are_unknown_not_zero_rooms():
    response = model_response()
    response["data"]["model"].update(rooms=None, floors=None, dimensionEstimates=None)
    result, _ = inspect(response)
    assert result["status"] == "ok"
    assert result["room_count"] is None
    assert result["floor_count"] is None
    assert result["rooms_with_positive_width_and_depth"] is None
    assert result["rooms"] == []
    assert result["field_availability"] == {
        "floors": False,
        "rooms": False,
        "model_dimension_estimates": False,
        "any_room_dimension_estimates": False,
        "legacy_model_dimensions": False,
        "any_room_legacy_dimensions": False,
    }


def test_known_empty_collections_have_zero_count():
    response = model_response()
    response["data"]["model"].update(rooms=[], floors=[], dimensionEstimates=None)
    result, _ = inspect(response)
    assert result["room_count"] == result["floor_count"] == 0
    assert result["field_availability"]["rooms"] is True


@pytest.mark.parametrize("value", [True, "3.0", -1, 10**400])
def test_invalid_numeric_dimensions_are_not_coerced(value):
    response = model_response()
    response["data"]["model"]["rooms"][0]["dimensionEstimates"]["width"] = value
    result, _ = inspect(response)
    assert result["status"] == "unavailable"
    assert "rooms" not in result


def test_imperial_response_cannot_be_mislabeled_metric():
    response = model_response()
    response["data"]["model"]["rooms"][0]["dimensionEstimates"]["units"] = "imperial"
    result, _ = inspect(response)
    assert result["reason"] == "invalid_dimension_units"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("model", "model_identity_mismatch"),
        ("room", "duplicate_room_identity"),
        ("floor", "duplicate_floor_identity"),
        ("foreign_floor", "room_floor_identity_mismatch"),
        ("missing_floors", "room_floor_identity_mismatch"),
    ],
)
def test_model_room_and_floor_identities_are_bound(change, reason):
    response = model_response()
    model = response["data"]["model"]
    if change == "model":
        model["id"] = "foreign-model"
    elif change == "room":
        model["rooms"] *= 2
    elif change == "floor":
        model["floors"] *= 2
    elif change == "foreign_floor":
        model["rooms"][0]["floor"]["id"] = "foreign-floor"
    else:
        model["floors"] = None
    result, _ = inspect(response)
    assert result["reason"] == reason
    assert "rooms" not in result


@pytest.mark.parametrize("collection,number", [("rooms", 201), ("floors", 21)])
def test_collection_limits_do_not_return_partial_counts(collection, number):
    response = model_response()
    response["data"]["model"][collection] = [{"id": str(i)} for i in range(number)]
    result, _ = inspect(response)
    assert result["reason"] == "model_collection_limit_or_shape"
    assert "room_count" not in result


@pytest.mark.parametrize(
    "body",
    [
        b'{"data":{},"data":{}}',
        b'{"data":{"model":{"id":NaN}}}',
        b"[1,2,3]",
        b"not-json",
        b"\xff",
        b"[" * 2000,
    ],
)
def test_malformed_or_ambiguous_json_is_generic(body):
    result, _ = inspect(httpx.Response(200, content=body))
    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_response"


def test_response_size_cap_is_explicit_without_partial_data():
    result, calls = inspect(max_response_bytes=10)
    assert len(calls) == 1
    assert result["reason"] == "response_limit_exceeded"
    assert "rooms" not in result


def test_transport_error_is_sanitized_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("synthetic-secret should not escape", request=request)

    client = MatterportClient(
        Catalog(), "id", "synthetic-secret", transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(client.inspect_floor_plan({}, 101, 7))
    assert len(calls) == 1
    assert result["reason"] == "provider_connection_error"
    assert "synthetic-secret" not in json.dumps(result)


def test_hard_deadline_stops_slow_transport():
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=model_response())

    client = MatterportClient(
        Catalog(), "id", "secret", timeout_seconds=0.02, transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(client.inspect_floor_plan({}, 101, 7))
    assert result["reason"] == "deadline_exceeded"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
        {"timeout_seconds": float("nan")},
        {"max_response_bytes": True},
        {"max_response_bytes": 0},
        {"max_response_bytes": 1024 * 1024 + 1},
    ],
)
def test_bounds_are_server_validated(options):
    with pytest.raises(ValueError):
        MatterportClient(Catalog(), "id", "secret", **options)


@pytest.mark.parametrize(
    "units,area_unit,length_unit",
    [
        ("metric", "square_meters", "meters"),
        ("imperial", "square_feet", "feet"),
    ],
)
def test_legacy_dimensions_preserve_separate_source_and_returned_units(
    units, area_unit, length_unit
):
    response = model_response()
    model = response["data"]["model"]
    legacy = {
        "units": units,
        "areaFloor": 120.5,
        "areaFloorIndoor": 100.25,
        "width": 0,
        "depth": None,
        "height": 8.0,
    }
    model["legacyDimensions"] = copy.deepcopy(legacy)
    model["rooms"][0]["legacyDimensions"] = copy.deepcopy(legacy)
    result, calls = inspect(response, include_legacy_dimensions=True)
    assert result["status"] == "ok"
    assert json.loads(calls[0].content)["query"] == LEGACY_DIMENSIONS_QUERY
    assert "areaFloor" not in DIMENSIONS_QUERY
    assert result["legacy_model_dimensions"]["area_floor"] == 120.5
    assert result["legacy_model_dimensions"]["source_field"] == "dimensions"
    assert result["legacy_model_dimensions"]["area_unit"] == area_unit
    assert result["legacy_model_dimensions"]["length_unit"] == length_unit
    assert result["rooms"][0]["legacy_dimensions"]["width"] == 0
    assert result["rooms"][0]["legacy_dimensions"]["depth"] is None
    assert result["model_dimension_estimates"]["area_square_meters"] == 60
    assert result["rooms_with_positive_legacy_area"] == 1
    assert result["geometry_clearance_verified"] is False


def test_legacy_values_never_backfill_modern_nulls():
    response = model_response()
    model = response["data"]["model"]
    model["dimensionEstimates"] = None
    model["rooms"][0]["dimensionEstimates"] = None
    model["rooms"][0]["legacyDimensions"] = {"units": "imperial", "areaFloor": 500.0}
    result, _ = inspect(response, include_legacy_dimensions=True)
    assert result["model_dimension_estimates"] is None
    assert result["rooms"][0]["dimension_estimates"] is None
    assert result["rooms_with_positive_width_and_depth"] == 0
    assert result["rooms_with_positive_legacy_area"] == 1
    assert result["rooms"][0]["legacy_dimensions"]["width"] is None


@pytest.mark.parametrize("units", [None, "feet", "square_feet", True, "unknown"])
def test_legacy_unknown_units_cannot_be_reported_as_measurements(units):
    response = model_response()
    response["data"]["model"]["legacyDimensions"] = {"units": units, "areaFloor": 10}
    result, _ = inspect(response, include_legacy_dimensions=True)
    assert result["reason"] == "invalid_dimension_units"


def test_basic_query_never_requests_legacy_even_when_server_option_enabled():
    result, calls = inspect(include_dimensions=False, include_legacy_dimensions=True)
    assert json.loads(calls[0].content)["query"] == BASIC_QUERY
    assert result["legacy_dimensions_requested"] is False
    assert result["legacy_model_dimensions"] is None


def test_room_display_cap_preserves_full_counts_with_explicit_truncation():
    response = model_response()
    template = response["data"]["model"]["rooms"][0]
    response["data"]["model"]["rooms"] = [
        dict(copy.deepcopy(template), id=f"room-{i:03}") for i in range(25)
    ]
    result, _ = inspect(response)
    assert result["room_count"] == 25
    assert result["rooms_with_positive_width_and_depth"] == 25
    assert result["rooms_returned"] == len(result["rooms"]) == 20
    assert result["rooms_truncated"] is True


def test_unicode_detail_byte_limit_preserves_counts_without_dropping_entire_evidence():
    response = model_response()
    model = response["data"]["model"]
    model["floors"][0]["id"] = "\U0001f3e0" * 128
    template = model["rooms"][0]
    template["label"] = "\U0001f3e0" * 200
    template["floor"]["id"] = model["floors"][0]["id"]
    template["legacyDimensions"] = {"units": "imperial", "areaFloor": 50.0}
    model["rooms"] = [
        dict(copy.deepcopy(template), id=f"{i:03}" + "\U0001f3e0" * 125) for i in range(20)
    ]
    result, _ = inspect(response, include_legacy_dimensions=True)
    assert result["status"] == "ok"
    assert result["room_count"] == 20
    assert result["rooms_with_positive_width_and_depth"] == 20
    assert 0 < result["rooms_returned"] < 20
    assert result["rooms_truncated"] is True
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 22_000


def test_total_deadline_covers_catalog_and_late_worker_cannot_call_provider():
    release = threading.Event()
    finished = threading.Event()
    provider_calls = []

    class SlowCatalog(Catalog):
        def search(self, criteria):
            release.wait(timeout=1)
            finished.set()
            return super().search(criteria)

    def handler(request):
        provider_calls.append(request)
        return httpx.Response(200, json=model_response())

    async def run():
        adapter = MatterportClient(
            SlowCatalog(),
            "id",
            "secret",
            timeout_seconds=0.03,
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await adapter.inspect_floor_plan({}, 101, 7)
            assert result["reason"] == "deadline_exceeded"
            assert result["status"] == "unavailable"
            assert "rooms" not in result
            assert provider_calls == []
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        await asyncio.sleep(0)
        assert provider_calls == []

    asyncio.run(run())


def test_caller_cancellation_propagates_and_closes_provider_stream():
    async def run():
        entered = asyncio.Event()
        closed = []

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.sleep(10)
                yield b"{}"

            async def aclose(self):
                closed.append(True)

        def handler(_):
            return httpx.Response(200, stream=Stream())

        adapter = MatterportClient(
            Catalog(), "id", "secret", transport=httpx.MockTransport(handler)
        )
        task = asyncio.create_task(adapter.inspect_floor_plan({}, 101, 7))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [True]

    asyncio.run(run())


def test_inspection_api_catalog_timeout_returns_unavailable_without_provider(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from spending_api.config import Settings
    from spending_api.main import create_app

    provider_calls = []

    def slow_search(self, criteria):
        time.sleep(1.15)
        return catalog_result()

    def handler(request):
        provider_calls.append(request)
        return httpx.Response(200, json=model_response())

    monkeypatch.setattr("spending_api.costar.CostarCatalog.search", slow_search)
    settings = Settings(
        _env_file=None,
        data_source="fixture",
        fixture_path=tmp_path / "missing-fixture",
        llm_enabled=False,
        costar_enabled=True,
        costar_data_dir=tmp_path / "missing-catalog",
        matterport_enabled=True,
        matterport_timeout_seconds=1,
        app_api_token="offline-token",
        matterport_api_key="offline-id",
        matterport_api_token="offline-secret",
    )
    with TestClient(
        create_app(settings, matterport_transport=httpx.MockTransport(handler))
    ) as client:
        response = client.post(
            "/api/v1/costar/inspect",
            headers={"Authorization": "Bearer offline-token"},
            json={"criteria": {}, "property_id": 101, "floor_plan_id": 7},
        )
        assert response.status_code == 503
        assert response.json()["status"] == "unavailable"
        assert response.json()["reason"] == "deadline_exceeded"
        assert response.headers["cache-control"] == "no-store"
    assert provider_calls == []
