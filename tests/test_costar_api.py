import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import SecretStr, ValidationError

from spending_api.config import Settings
from spending_api.costar import FILES
from spending_api.main import create_app
from spending_generator.cli import build_fixture

HEADERS = {"Authorization": "Bearer catalog-test-token"}


@pytest.fixture
def catalog_settings(tmp_path):
    kit = tmp_path / "kit"
    (kit / "data").mkdir(parents=True)
    listing = {
        "id": 10,
        "propertyId": 20,
        "address": {"state": "TX", "city": "Houston"},
        "buildingName": "Synthetic Place",
        "lifestyleType": 1,
        "apartmentModels": [
            {
                "id": 30,
                "name": "Synthetic Plan",
                "numBeds": 1,
                "minRent": 1200,
                "maxRent": 1700,
                "monetaryUnit": 1,
                "rentPricingType": 1,
                "numUnitsAvailable": 1,
                "minSquareFeet": 700,
            }
        ],
    }
    property_row = {
        "id": 20,
        "primaryAddress": {"state": "TX", "city": "Houston"},
        "propertyApartment": {
            "models": [
                {
                    "id": 30,
                    "attachments": [
                        {"videoExternalReferenceType": 36, "videoKey": "synthetic-tour"}
                    ],
                }
            ]
        },
    }
    (kit / FILES[0]).write_text(json.dumps(listing) + "\n", encoding="utf-8")
    (kit / FILES[1]).write_text(json.dumps(property_row) + "\n", encoding="utf-8")
    (kit / FILES[2]).write_text(
        "PropertyId,MatterportModelId\n20,synthetic-tour\n", encoding="utf-8"
    )
    fixture = tmp_path / "spending.json"
    fixture.write_text(json.dumps(build_fixture(42, 90, date(2026, 11, 13))), encoding="utf-8")
    return Settings(
        _env_file=None,
        data_source="fixture",
        fixture_path=fixture,
        llm_enabled=False,
        costar_enabled=True,
        costar_data_dir=kit,
        app_api_token="catalog-test-token",
    )


def test_catalog_route_uses_bound_kit_without_llm_or_bank_calls(catalog_settings, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Catalog endpoint must not load spending data or invoke a model")

    monkeypatch.setattr("spending_api.main.load_snapshot", forbidden)
    monkeypatch.setattr("spending_api.main.answer_spending", forbidden)
    with TestClient(create_app(catalog_settings)) as client:
        result = client.post(
            "/api/v1/costar/search",
            headers=HEADERS,
            json={"state": "TX", "max_monthly_rent_usd": 1500},
        )
        assert result.status_code == 200
        body = result.json()
        assert body["source_kind"] == "costar_local_snapshot"
        assert body["snapshot_date_basis"] == "unknown"
        assert body["coverage"]["matched_floor_plan_count"] == 1
        row = body["results"][0]
        assert row["asking_rent"]["maximum"] == 1700
        assert row["price_range_within_budget"] is False
        assert row["availability_is_live"] is False
        assert row["matterport"]["interior_observation_performed"] is False
        assert row["matterport"]["scope"] == "floor_plan"
        assert result.headers["cache-control"] == "no-store"
        assert str(catalog_settings.costar_data_dir) not in result.text


def test_catalog_rejects_query_parameter_overrides(catalog_settings):
    with TestClient(create_app(catalog_settings)) as client:
        response = client.post(
            "/api/v1/costar/search?limit=999&data_dir=private", headers=HEADERS, json={"limit": 1}
        )
        assert response.status_code == 422


def test_catalog_auth_happens_before_parsing_or_loading(catalog_settings, monkeypatch):
    monkeypatch.setattr(
        "spending_api.costar.CostarCatalog._load", lambda _: pytest.fail("unauthenticated read")
    )
    with TestClient(create_app(catalog_settings)) as client:
        assert client.post("/api/v1/costar/search", content="invalid").status_code == 401


@pytest.mark.parametrize(
    "filters",
    [
        {"data_dir": "private"},
        {"state": "NY"},
        {"limit": 100},
        {"bedrooms": True},
        {"max_monthly_rent_usd": "1500"},
        {"snapshot_date": "today"},
    ],
)
def test_catalog_rejects_invalid_filters_without_echo(catalog_settings, filters):
    with TestClient(create_app(catalog_settings)) as client:
        response = client.post("/api/v1/costar/search", headers=HEADERS, json=filters)
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid catalog search filters"}


def test_missing_catalog_is_not_empty_success(catalog_settings, tmp_path):
    catalog_settings.costar_data_dir = tmp_path / "missing"
    with TestClient(create_app(catalog_settings)) as client:
        response = client.post("/api/v1/costar/search", headers=HEADERS, json={})
        assert response.status_code == 503
        assert response.json()["status"] == "unavailable"
        assert response.json()["results"] == []


def test_catalog_disabled_and_enabled_auth_requirement(tmp_path):
    with pytest.raises(ValidationError, match="APP_API_TOKEN"):
        Settings(_env_file=None, data_source="fixture", costar_enabled=True, app_api_token="")
    settings = Settings(
        _env_file=None,
        data_source="fixture",
        llm_enabled=False,
        costar_enabled=False,
        costar_data_dir=tmp_path / "missing",
    )
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/costar/search", json={}).status_code == 503


def test_agent_can_search_catalog_with_source_evidence(catalog_settings):
    class CatalogModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            assert "costar_search" in {tool.name for tool in tools}
            return self

        async def ainvoke(self, messages, config=None):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "costar_search",
                            "id": "catalog-1",
                            "args": {"state": "TX", "limit": 1},
                        }
                    ],
                )
            result = json.loads(messages[-1].content)
            assert result["results"][0]["floor_plan_id"] == 30
            return AIMessage(content="One recorded match; rent and availability need confirmation.")

    settings = catalog_settings.model_copy(
        update={"llm_enabled": True, "gemini_api_key": "fake-key"}
    )
    with TestClient(create_app(settings, agent_model=CatalogModel())) as client:
        result = client.post(
            "/api/v1/agent/answer",
            headers=HEADERS,
            json={"question": "Find a one-bedroom in Texas under $1800."},
        )
        assert result.status_code == 200
        assert result.json()["status"] == "answered"
        evidence = result.json()["evidence"][0]
        assert evidence["tool"] == "costar_search"
        assert evidence["result"]["source_kind"] == "costar_local_snapshot"


def spatial_settings(settings):
    return settings.model_copy(
        update={
            "matterport_enabled": True,
            "matterport_api_key": SecretStr("test-id"),
            "matterport_api_token": SecretStr("test-secret"),
        }
    )


def test_inspection_route_reads_only_catalog_bound_model(catalog_settings):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.matterport.com/api/models/graph"
        payload = json.loads(request.content)
        assert payload["variables"] == {"modelId": "synthetic-tour"}
        assert "mutation" not in payload["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "model": {
                        "id": "synthetic-tour",
                        "state": "active",
                        "modified": None,
                        "floors": [{"id": "floor-one"}],
                        "dimensionEstimates": None,
                        "rooms": [
                            {
                                "id": "room-one",
                                "label": "Study",
                                "floor": {"id": "floor-one"},
                                "dimensionEstimates": {
                                    "units": "metric",
                                    "area": 12.0,
                                    "areaIndoor": 12.0,
                                    "width": 3.0,
                                    "depth": 4.0,
                                    "height": None,
                                },
                            }
                        ],
                    }
                }
            },
        )

    with TestClient(
        create_app(
            spatial_settings(catalog_settings), matterport_transport=httpx.MockTransport(handler)
        )
    ) as client:
        response = client.post(
            "/api/v1/costar/inspect",
            headers=HEADERS,
            json={
                "criteria": {},
                "property_id": 20,
                "floor_plan_id": 30,
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "ok"
        assert result["source_kind"] == "matterport_model_api"
        assert result["geometry_clearance_verified"] is False
        assert result["rooms"][0]["dimension_estimates"]["width_meters"] == 3.0
        assert response.headers["cache-control"] == "no-store"
        assert "test-secret" not in response.text
    assert len(calls) == 1


def test_inspection_auth_and_selection_prevent_network_calls(catalog_settings):
    def forbidden(_):
        pytest.fail("Invalid selection must not call Matterport")

    with TestClient(
        create_app(
            spatial_settings(catalog_settings), matterport_transport=httpx.MockTransport(forbidden)
        )
    ) as client:
        assert client.post("/api/v1/costar/inspect", content="bad").status_code == 401
        invalid = client.post(
            "/api/v1/costar/inspect",
            headers=HEADERS,
            json={
                "criteria": {},
                "property_id": 20,
                "floor_plan_id": 30,
                "model_id": "arbitrary",
            },
        )
        assert invalid.status_code == 422
        unknown = client.post(
            "/api/v1/costar/inspect",
            headers=HEADERS,
            json={
                "criteria": {},
                "property_id": 999,
                "floor_plan_id": 30,
            },
        )
        assert unknown.status_code == 422
        assert unknown.json()["status"] == "selection_rejected"


def test_inspection_disabled_returns_actionable_error(catalog_settings):
    with TestClient(create_app(catalog_settings)) as client:
        response = client.post("/api/v1/costar/inspect", headers=HEADERS, json={})
        assert response.status_code == 503
        assert "disabled" in response.json()["detail"]


def test_openapi_request_references_resolve(catalog_settings):
    with TestClient(create_app(catalog_settings)) as client:
        document = client.get("/openapi.json").json()

    def check(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/")
                target = document
                for segment in value["$ref"][2:].split("/"):
                    target = target[segment]
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(document)


def test_agent_shortlist_then_inspect_preserves_measurement_provenance(catalog_settings):
    class SpatialModel:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            assert {"costar_search", "inspect_floor_plan"} <= {tool.name for tool in tools}
            return self

        async def ainvoke(self, messages, config=None):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "costar_search",
                            "id": "search",
                            "args": {"limit": 1},
                        }
                    ],
                )
            if self.calls == 2:
                selected = json.loads(messages[-1].content)["results"][0]
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "inspect_floor_plan",
                            "id": "inspect",
                            "args": {
                                "criteria": {"limit": 1},
                                "property_id": selected["property_id"],
                                "floor_plan_id": selected["floor_plan_id"],
                            },
                        }
                    ],
                )
            evidence = json.loads(messages[-1].content)
            room = evidence["rooms"][0]
            assert room["dimension_estimates"] is None
            assert room["legacy_dimensions"]["area_unit"] == "square_feet"
            assert room["legacy_dimensions"]["area_floor"] == 100
            return AIMessage(content="The scan reports 100 square feet; desk clearance is unknown.")

    def handler(request):
        assert "legacyDimensions" in json.loads(request.content)["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "model": {
                        "id": "synthetic-tour",
                        "state": "active",
                        "floors": [{"id": "f"}],
                        "dimensionEstimates": None,
                        "legacyDimensions": None,
                        "rooms": [
                            {
                                "id": "r",
                                "label": "Study",
                                "floor": {"id": "f"},
                                "dimensionEstimates": None,
                                "legacyDimensions": {
                                    "units": "imperial",
                                    "areaFloor": 100,
                                    "areaFloorIndoor": 100,
                                    "width": None,
                                    "depth": None,
                                    "height": None,
                                },
                            }
                        ],
                    }
                }
            },
        )

    settings = spatial_settings(catalog_settings).model_copy(
        update={
            "llm_enabled": True,
            "gemini_api_key": SecretStr("fake-model-key"),
        }
    )
    with TestClient(
        create_app(
            settings, agent_model=SpatialModel(), matterport_transport=httpx.MockTransport(handler)
        )
    ) as client:
        response = client.post(
            "/api/v1/agent/answer",
            headers=HEADERS,
            json={"question": "Find a one-bedroom and inspect its room areas."},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "answered"
        assert result["model_calls"] == 3 and result["tool_calls"] == 2
        search, inspection = result["evidence"]
        assert inspection["result"]["catalog_source_hash"] == search["result"]["source_hash"]
        assert inspection["result"]["geometry_clearance_verified"] is False
