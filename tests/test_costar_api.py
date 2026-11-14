import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import ValidationError

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
