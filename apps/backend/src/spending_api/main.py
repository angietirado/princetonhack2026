import secrets
import sqlite3
from datetime import date
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from standardwebhooks.webhooks import Webhook, WebhookVerificationError

from spending_api.agent_tools import SpendingTools
from spending_api.config import Settings, load_settings
from spending_api.dashboard import build_dashboard
from spending_api.models import (
    Account,
    DashboardData,
    DateRange,
    RecurringCharge,
    SpendingSummary,
    Transaction,
)
from spending_api.nessie import DataSourceError, load_snapshot
from spending_api.photon import enqueue_event, parse_incoming


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Spending Companion API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["Authorization", "Content-Type"],
    )
    bearer = HTTPBearer(auto_error=False)

    def authorize(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        expected = settings.app_api_token.get_secret_value()
        if expected and (
            not credentials
            or not secrets.compare_digest(
                credentials.credentials,
                expected,
            )
        ):
            raise HTTPException(401, "Valid app bearer token required")

    async def scoped_tools(_: Annotated[None, Depends(authorize)]) -> SpendingTools:
        try:
            return SpendingTools(await load_snapshot(settings))
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Nessie request failed; check backend configuration") from exc
        except DataSourceError as exc:
            raise HTTPException(503, str(exc)) from exc

    def window(start: date | None = None, end: date | None = None) -> DateRange:
        if start and end and start > end:
            raise HTTPException(422, "start must be on or before end")
        return DateRange(start=start, end=end)

    @app.get("/health")
    def health():
        return {"status": "ok", "data_source": settings.data_source}

    @app.get("/api/v1/accounts", response_model=list[Account])
    async def accounts(tools: Annotated[SpendingTools, Depends(scoped_tools)]):
        return tools.snapshot.accounts

    @app.get("/api/v1/dashboard", response_model=DashboardData)
    async def dashboard(
        response: Response,
        tools: Annotated[SpendingTools, Depends(scoped_tools)],
        dates: Annotated[DateRange, Depends(window)],
    ):
        response.headers["Cache-Control"] = "no-store"
        return build_dashboard(tools, dates)

    @app.get("/api/v1/transactions", response_model=list[Transaction])
    async def transactions(
        tools: Annotated[SpendingTools, Depends(scoped_tools)],
        dates: Annotated[DateRange, Depends(window)],
    ):
        return tools.get_transactions(dates)

    @app.get("/api/v1/spending/summary", response_model=SpendingSummary)
    async def summary(
        tools: Annotated[SpendingTools, Depends(scoped_tools)],
        dates: Annotated[DateRange, Depends(window)],
    ):
        return tools.summarize_spending(dates)

    @app.get("/api/v1/spending/recurring", response_model=list[RecurringCharge])
    async def recurring(
        tools: Annotated[SpendingTools, Depends(scoped_tools)],
        dates: Annotated[DateRange, Depends(window)],
    ):
        return tools.find_recurring_charges(dates)

    @app.post("/webhooks/photon", status_code=204, response_class=Response)
    async def photon_webhook(request: Request):
        if not settings.photon_enabled or not all(
            [
                settings.photon_webhook_secret.get_secret_value(),
                settings.photon_agent_user_id,
                settings.photon_allowed_sender_id,
            ]
        ):
            raise HTTPException(503, "Photon webhook receiver is not configured")
        # Bound the body before parsing, without trusting Content-Length.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 256_000:
                raise HTTPException(413, "Event exceeds the demo size limit")
        try:
            event = Webhook(settings.photon_webhook_secret.get_secret_value()).verify(
                bytes(raw),
                dict(request.headers),
            )
            if not isinstance(event, dict) or event.get("id") != request.headers.get("webhook-id"):
                raise ValueError("Event ID does not match signed header")
            incoming = parse_incoming(event, settings)
        except (WebhookVerificationError, ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(400, "Invalid Photon webhook") from exc
        if incoming:
            try:
                enqueue_event(
                    settings.photon_inbox_path,
                    event["id"],
                    incoming.message_id,
                    event,
                )
            except (sqlite3.Error, OSError) as exc:
                raise HTTPException(503, "Unable to persist event; retry later") from exc
        return Response(status_code=204)

    return app
