"""Offline-first HTTP boundary for Blockwise participant collection and analytics."""

import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from spending_api.lab_chat import register_chat_routes
from spending_api.lab_engine import evaluate, recommend
from spending_api.lab_models import (
    Catalog,
    ChoiceRequest,
    DataMode,
    EvaluationRequest,
    RecommendationRequest,
    SessionRequest,
)
from spending_api.lab_store import LabConflict, LabNotFound, LabStore, LabUnauthorized

SIMULATION_NOTICE = (
    "Home locations come from the supplied CoStar sample. Stores, prices, budgets, and travel "
    "are simulated. Choices are observations from this exercise, not actual household spending."
)
_bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON fields")
        value[key] = item
    return value


def create_app(
    *,
    scenarios_path: str | Path | None = None,
    db_path: str | Path | None = None,
    admin_token: str | None = None,
    chat_interpreter=None,
    chat_timeout_seconds: float = 12.0,
) -> FastAPI:
    """No dotenv loading or live provider calls, including at startup."""
    source = Path(scenarios_path or os.environ.get("LAB_SCENARIOS_PATH", "data/scenarios.json"))
    catalog = Catalog.model_validate_json(source.read_text(encoding="utf-8-sig"))
    scenarios = {item.id: item.model_dump(mode="json") for item in catalog.scenarios}
    store = LabStore(db_path or os.environ.get("LAB_DB_PATH", ".data/lab.sqlite3"))
    admin_key = os.environ.get("LAB_ADMIN_TOKEN", "") if admin_token is None else admin_token
    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            application.state.lab_chat.gate.close()

    app = FastAPI(
        lifespan=lifespan,
        title="Blockwise shopping experiment", version="0.1.0",
        description=(
            "Local experiment API. Participant sessions require their own access token. "
            "Analytics requires LAB_ADMIN_TOKEN when configured; otherwise it is open to "
            "the local deployment. Do not expose an unprotected analytics server publicly."
        ),
    )
    app.state.lab_store = store
    app.state.lab_scenarios = scenarios
    app.state.lab_admin_protected = bool(admin_key)

    @app.middleware("http")
    async def request_limits(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            content_type = request.headers.get("content-type", "").split(";")[0].lower().strip()
            if content_type != "application/json":
                return JSONResponse({"detail": "Use application/json"}, status_code=415)
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16384:
                    return JSONResponse({"detail": "Request exceeds 16 KiB"}, status_code=413)
                chunks.append(chunk)
            raw = b"".join(chunks)
            try:
                json.loads(
                    raw.decode("utf-8"), object_pairs_hook=_unique_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
                )
            except (ValueError, UnicodeError, RecursionError):
                return JSONResponse({"detail": "Invalid JSON request"}, status_code=422)
            # Starlette's cached request body is replayed to downstream request parsing.
            request._body = raw
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        # Do not echo request bodies, credentials, or experimental answers in error details.
        return JSONResponse(
            {"detail": "Request fields are invalid. Check the required values and limits."},
            status_code=422,
        )

    @app.exception_handler(LabConflict)
    async def conflict(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(LabNotFound)
    async def not_found(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(LabUnauthorized)
    async def unauthorized(_request, error):
        return JSONResponse(
            {"detail": str(error)}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(_request, _error):
        return JSONResponse(
            {"detail": "The experiment store is temporarily unavailable. Please retry."},
            status_code=503, headers={"Retry-After": "2"},
        )

    def require_admin(credentials: Credentials):
        token = credentials.credentials if credentials else ""
        if admin_key and not secrets.compare_digest(admin_key.encode(), token.encode()):
            raise LabUnauthorized("An analytics access token is required")

    def session_for(session_id: UUID, credentials: HTTPAuthorizationCredentials | None):
        return store.session(str(session_id), credentials.credentials if credentials else None)

    def calculated(session: dict, body: EvaluationRequest):
        constraints = (
            body.constraints.model_dump(mode="json") if body.constraints else session["constraints"]
        )
        try:
            return evaluate(session["scenario"], body.assignments, constraints)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/health")
    def health():
        return {"status": "ok", "app": "blockwise"}

    @app.get("/api/v1/scenarios")
    def list_scenarios():
        return {"scenarios": list(scenarios.values()), "simulation_notice": SIMULATION_NOTICE}

    @app.post("/api/v1/sessions", status_code=201)
    def create_session(body: SessionRequest):
        scenario = scenarios.get(body.scenario_id)
        if scenario is None:
            raise LabNotFound("Scenario not found")
        return store.create_session(body.model_dump(mode="json"), scenario)

    @app.get("/api/v1/sessions/{session_id}")
    def get_session(session_id: UUID, credentials: Credentials):
        return session_for(session_id, credentials)

    @app.post("/api/v1/sessions/{session_id}/evaluate")
    def evaluate_choice(session_id: UUID, body: EvaluationRequest, credentials: Credentials):
        return calculated(session_for(session_id, credentials), body)

    @app.post("/api/v1/sessions/{session_id}/choices", status_code=201)
    def save_choice(session_id: UUID, body: ChoiceRequest, credentials: Credentials):
        session = session_for(session_id, credentials)
        result = calculated(session, body)
        return store.add_choice(str(session_id), body.model_dump(mode="json"), result)

    @app.post("/api/v1/sessions/{session_id}/recommendations")
    def recommendations(
        session_id: UUID, body: RecommendationRequest, credentials: Credentials
    ):
        session = session_for(session_id, credentials)
        if not any(choice["stage"] == "initial" for choice in session["choices"]):
            raise LabConflict("Submit your initial choice before revealing alternatives")
        constraints = (
            body.constraints.model_dump(mode="json") if body.constraints else session["constraints"]
        )
        return recommend(session["scenario"], constraints)

    @app.get("/api/v1/analytics/summary", dependencies=[Depends(require_admin)])
    def analytics_summary(
        scenario_id: str | None = Query(default=None, max_length=80),
        data_mode: DataMode = "participants",
    ):
        return store.summary(scenario_id, data_mode)

    @app.get("/api/v1/analytics/sessions", dependencies=[Depends(require_admin)])
    def analytics_sessions(
        scenario_id: str | None = Query(default=None, max_length=80),
        data_mode: DataMode = "participants",
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ):
        return store.sessions(scenario_id, data_mode, limit, offset)

    @app.get("/api/v1/analytics/sessions/{session_id}", dependencies=[Depends(require_admin)])
    def analytics_session(session_id: UUID):
        return store.session(str(session_id), admin=True)

    @app.get("/api/v1/analytics/export", dependencies=[Depends(require_admin)])
    def export(
        scenario_id: str | None = Query(default=None, max_length=80),
        data_mode: DataMode = "participants",
    ):
        return Response(
            store.export(scenario_id, data_mode),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="blockwise-choices.csv"'},
        )

    register_chat_routes(
        app, store, interpreter=chat_interpreter, timeout_seconds=chat_timeout_seconds
    )
    return app



