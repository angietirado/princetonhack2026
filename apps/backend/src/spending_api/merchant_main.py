"""Standalone merchant API: explicit runtime configuration, scoped immutable snapshots."""
import asyncio
import json
import os
import secrets
import sqlite3
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from spending_api import merchant_ai
from spending_api.merchant import DEMO, Dataset, build_dashboard, csv_export, investigate
from spending_api.merchant_compare import compare_periods
from spending_api.merchant_import import DatasetStore, parse_csv
from spending_api.merchant_experiments import plan_experiment

TEMPLATE = "kind,id,customer_id,order_id,date,amount_cents,status\norder,order_001,customer_001,,2026-11-01,1250,completed\norder,order_002,customer_001,,2026-11-08,1500,completed\nrefund,refund_001,,order_001,2026-11-09,250,\n"


def _iso_day(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Use an explicit ISO calendar date YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Use an explicit ISO calendar date YYYY-MM-DD")
    return parsed

class OwnerQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    question: str = Field(min_length=1, max_length=2000)
    start: date | None = None
    end: date | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def calendar_date(cls, value):
        return _iso_day(value)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Question cannot be blank")
        return value


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    csv: str = Field(min_length=1, max_length=750_000)
    business_name: str = Field(min_length=1, max_length=100)
    synthetic: bool = Field(default=False, strict=True)
    coverage_start: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    coverage_end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class CommitRequest(ImportRequest):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    segment: Literal["champions", "regulars", "promising", "slipping", "new"]
    max_customers: int = Field(strict=True, ge=1, le=1000)
    offer_cents: int = Field(strict=True, ge=0, le=5000)
    window_days: int = Field(strict=True, ge=7, le=90)
    budget_cents: int | None = Field(default=None, strict=True, ge=0, le=5_000_000)
    start: date | None = None
    end: date | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def calendar_date(cls, value):
        return _iso_day(value)

class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    dataset_id: str = Field(pattern=r"^(demo|[a-f0-9]{64})$")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON fields")
        result[key] = value
    return result


async def _body(request: Request, schema, *, limit=800_000):
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise HTTPException(403, "Invalid request origin") from None
        if (parsed.scheme not in {"http", "https"} or parsed.scheme != request.url.scheme
                or parsed.netloc.casefold() != request.headers.get("host", "").casefold()
                or parsed.path or parsed.query or parsed.fragment or parsed.username):
            raise HTTPException(403, "Use this app's same-origin interface")
    if request.query_params:
        raise HTTPException(422, "Provide supported fields in the JSON body")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Use application/json")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HTTPException(413, "Request exceeds the size limit")
    try:
        return schema.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
    except (ValidationError, ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "Invalid request fields; use the documented JSON format") from None


def create_app(*, dataset: Dataset = DEMO, token: str | None = None,
               data_dir: str | Path | None = None) -> FastAPI:
    expected_token = os.environ.get("OWNER_API_TOKEN", "") if token is None else token
    directory = data_dir if data_dir is not None else os.environ.get("OWNER_DATA_DIR")
    store = DatasetStore(directory) if directory else None
    app = FastAPI(title="Regulars merchant insights", version="0.1.0")
    bearer = HTTPBearer(auto_error=False)
    ai_lock = asyncio.Lock()

    def authorize(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if expected_token and (
            len(request.headers.getlist("authorization")) != 1 or not credentials
            or not secrets.compare_digest(credentials.credentials.encode(), expected_token.encode())
        ):
            raise HTTPException(401, "Valid owner bearer token required")

    def active_dataset():
        try:
            if store is not None:
                key, selected = store.active()
                return key, selected if selected is not None else dataset
            return "demo", dataset
        except (OSError, sqlite3.Error, ValueError):
            raise HTTPException(503, "Selected dataset is unavailable; review local dataset storage") from None

    def snapshot(start=None, end=None):
        key, selected = active_dataset()
        try:
            start = _iso_day(start) if isinstance(start, str) else start
            end = _iso_day(end) if isinstance(end, str) else end
            result = build_dashboard(start, end, data=selected)
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, "Dates cannot be analyzed" if isinstance(exc, OverflowError) else str(exc)) from None
        result["source"]["dataset_id"] = key
        return result

    def require_store():
        if store is None:
            raise HTTPException(503, "CSV imports require configured local dataset storage")
        return store

    def parse(body):
        try:
            return parse_csv(body.csv, business_name=body.business_name, synthetic=body.synthetic,
                             coverage_start=body.coverage_start, coverage_end=body.coverage_end)
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from None

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "application": "regulars"}

    @app.get("/api/v1/owner/status", dependencies=[Depends(authorize)])
    def status():
        _, selected = active_dataset()
        ai = merchant_ai.status()
        if ai["enabled"] and not expected_token:
            ai = {**ai, "available": False, "reason": "owner_token_required"}
        return {"ready": True, "synthetic": selected.synthetic, "authentication_required": bool(expected_token),
                "investigation": "ai" if ai["enabled"] else "deterministic", "ai_enabled": ai["enabled"],
                "ai": ai, "can_export": True, "can_import": store is not None, "read_only": store is None}

    @app.get("/api/v1/owner/dashboard", dependencies=[Depends(authorize)])
    def dashboard(start: str | None = None, end: str | None = None):
        return snapshot(start, end)

    @app.get("/api/v1/owner/compare", dependencies=[Depends(authorize)])
    def comparison(start: str | None = None, end: str | None = None):
        key, selected = active_dataset()
        try:
            result = compare_periods(selected, _iso_day(start), _iso_day(end))
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, "Dates cannot be compared" if isinstance(exc, OverflowError) else str(exc)) from None
        result["source"]["dataset_id"] = key
        return result

    @app.get("/api/v1/owner/export.csv", dependencies=[Depends(authorize)])
    def export(start: str | None = None, end: str | None = None):
        data = snapshot(start, end)
        return Response(csv_export(data), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="regulars-customers.csv"',
                                 "X-Source-Revision": data["source"]["revision"],
                                 "X-Dataset-ID": data["source"]["dataset_id"]})

    @app.get("/api/v1/owner/datasets", dependencies=[Depends(authorize)])
    def datasets():
        key, selected = active_dataset()
        try:
            rows = store.list() if store else [{"id": "demo", "business_name": selected.business_name,
                    "synthetic": selected.synthetic, "label": selected.label,
                    "coverage_start": selected.coverage_start.isoformat(), "coverage_end": selected.coverage_end.isoformat()}]
        except (OSError, sqlite3.Error, ValueError):
            raise HTTPException(503, "Dataset list is unavailable") from None
        return {"active_id": key, "datasets": rows}

    @app.get("/api/v1/owner/import/template.csv", dependencies=[Depends(authorize)])
    def template():
        return Response(TEMPLATE, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="regulars-import-template.csv"'})

    @app.post("/api/v1/owner/import/preview", dependencies=[Depends(authorize)])
    async def preview(request: Request):
        require_store()
        body = await _body(request, ImportRequest)
        _, result = await run_in_threadpool(parse, body)
        return result

    @app.post("/api/v1/owner/import/commit", dependencies=[Depends(authorize)])
    async def commit(request: Request):
        storage = require_store()
        body = await _body(request, CommitRequest)
        data, reviewed = await run_in_threadpool(parse, body)
        try:
            key = await run_in_threadpool(storage.add, data, body.preview_hash)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except (OSError, sqlite3.Error):
            raise HTTPException(503, "Import storage is unavailable; check the dataset list before retrying") from None
        return {"dataset_id": key, "active_id": key, "preview": reviewed}

    @app.post("/api/v1/owner/datasets/select", dependencies=[Depends(authorize)])
    async def select(request: Request):
        storage = require_store()
        body = await _body(request, SelectionRequest)
        try:
            await run_in_threadpool(storage.select, body.dataset_id)
        except ValueError:
            raise HTTPException(404, "Dataset not found") from None
        except (OSError, sqlite3.Error):
            raise HTTPException(503, "Dataset selection is unavailable; refresh before retrying") from None
        return {"dataset_id": body.dataset_id, "active_id": body.dataset_id}

    @app.post("/api/v1/owner/experiments/plan", dependencies=[Depends(authorize)])
    async def experiment(request: Request):
        body = await _body(request, ExperimentRequest, limit=8192)
        current = await run_in_threadpool(snapshot, body.start, body.end)
        try:
            result = plan_experiment(current, segment=body.segment, max_customers=body.max_customers,
                                     offer_cents=body.offer_cents, window_days=body.window_days,
                                     budget_cents=body.budget_cents)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        result["source"] = current["source"]
        return result
    @app.post("/api/v1/owner/ask", dependencies=[Depends(authorize)])
    async def ask(request: Request):
        question = await _body(request, OwnerQuestion, limit=8192)
        current = await run_in_threadpool(snapshot, question.start, question.end)
        capability = merchant_ai.status()
        if not capability["enabled"]:
            return investigate(question.question, current)
        if not expected_token:
            raise HTTPException(503, "Configure OWNER_API_TOKEN before enabling AI requests")
        if ai_lock.locked():
            raise HTTPException(429, "An AI investigation is already running; retry shortly")
        async with ai_lock:
            result = await merchant_ai.investigate(current, question.question)
        result["source"] = current["source"]
        result["period"] = current["period"]
        return result

    return app
