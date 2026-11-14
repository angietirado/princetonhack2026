"""Validated inputs for the local shopping experiment; all money is integer cents."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Constraints(InputModel):
    budget_cents: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    time_budget_minutes: float = Field(gt=0, le=1440, allow_inf_nan=False)
    travel_mode: Literal["walk", "drive"]
    max_stores: Annotated[StrictInt, Field(ge=1, le=2)]

    @field_validator("time_budget_minutes", mode="before")
    @classmethod
    def numeric_time(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Time must be a number")
        return value


class Home(BaseModel):
    model_config = ConfigDict(extra="allow")
    label: str = Field(min_length=1, max_length=300)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Product(InputModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=150)
    quantity: Annotated[StrictInt, Field(ge=1, le=100)]
    unit: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)


class Store(InputModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=150)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    shopping_minutes: float = Field(ge=0, le=240, allow_inf_nan=False)
    prices: dict[Identifier, Annotated[StrictInt, Field(ge=0, le=1_000_000)]]


class Travel(InputModel):
    method: Literal["modeled_distance"]
    road_factor: float = Field(ge=1, le=5, allow_inf_nan=False)
    walk_kmh: float = Field(gt=0, le=10, allow_inf_nan=False)
    drive_kmh: float = Field(gt=0, le=150, allow_inf_nan=False)
    drive_cost_cents_per_km: float = Field(ge=0, le=1000, allow_inf_nan=False)
    drive_parking_minutes_per_stop: float = Field(ge=0, le=60, allow_inf_nan=False)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Identifier
    version: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=3000)
    home: Home
    defaults: Constraints
    basket: list[Product] = Field(min_length=1, max_length=10)
    stores: list[Store] = Field(min_length=1, max_length=5)
    travel: Travel
    provenance: dict[str, str]

    @model_validator(mode="after")
    def complete_catalog(self):
        products = {item.id for item in self.basket}
        stores = {store.id for store in self.stores}
        if len(products) != len(self.basket) or len(stores) != len(self.stores):
            raise ValueError("Product and store identifiers must be unique")
        if "home" in stores:
            raise ValueError("The store identifier home is reserved")
        if any(set(store.prices) != products for store in self.stores):
            raise ValueError("Every store must price every basket product exactly once")
        return self


class Catalog(InputModel):
    schema_version: Literal[1]
    scenarios: list[Scenario] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.id for item in self.scenarios}) != len(self.scenarios):
            raise ValueError("Scenario identifiers must be unique")
        return self


class SessionRequest(InputModel):
    scenario_id: Identifier
    constraints: Constraints
    is_demo: StrictBool = False
    consent: StrictBool
    request_id: UUID

    @field_validator("consent")
    @classmethod
    def consent_required(cls, value):
        if not value:
            raise ValueError("Consent to save this simulation is required")
        return value


class EvaluationRequest(InputModel):
    assignments: dict[Identifier, Identifier] = Field(min_length=1, max_length=10)
    constraints: Constraints | None = None


class ChoiceRequest(EvaluationRequest):
    request_id: UUID
    stage: Literal["initial", "revised"]


class RecommendationRequest(InputModel):
    constraints: Constraints | None = None


DataMode = Literal["participants", "demo", "all"]

