import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager, suppress
from datetime import date
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from standardwebhooks.webhooks import Webhook, WebhookVerificationError
from starlette.concurrency import run_in_threadpool

from spending_api.agent_tools import SpendingTools
from spending_api.comparison import ComparisonRequest, SpendingComparison, compare_spending
from spending_api.config import Settings, load_settings
from spending_api.conversation import (
    CONVERSATION_SWEEP_SECONDS,
    ConversationBusyError,
    ConversationReference,
    ConversationResetError,
    ConversationScope,
    ConversationStore,
)
from spending_api.conversation_contract import (
    ClearConversation,
    ContinueConversation,
    ConversationReceipt,
    ConversationRequest,
)
from spending_api.costar import CostarCatalog, CostarSearchCriteria
from spending_api.dashboard import build_dashboard
from spending_api.llm import AgentAnswer, answer_spending
from spending_api.models import (
    Account,
    DashboardData,
    DateRange,
    RecurringCharge,
    SpendingSummary,
    Transaction,
)
from spending_api.nessie import DataSourceError, load_snapshot
from spending_api.photon import enqueue_event, parse_incoming, scope_hash


class AgentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    question: str = Field(min_length=1, max_length=2000)
    conversation: ConversationRequest | None = None

    @field_validator("question")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question cannot be blank")
        return value


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _comparison_schema():
    schema = ComparisonRequest.model_json_schema()
    definitions = schema.pop("$defs", {})
    for name in ("baseline", "current"):
        reference = schema["properties"][name].get("$ref")
        if reference:
            schema["properties"][name] = definitions[reference.rsplit("/", 1)[-1]]
    return schema


def _inline_request_schema(model: type[BaseModel]) -> dict:
    """Inline request-only models not registered as FastAPI body parameters."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value):
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            resolved = resolve(definitions[reference.rsplit("/", 1)[-1]])
            resolved.update({key: resolve(item) for key, item in value.items() if key != "$ref"})
            return resolved
        # Literal tags in the inlined oneOf still discriminate the alternatives;
        # the original discriminator mapping would point at document-root $defs.
        return {key: resolve(item) for key, item in value.items() if key != "discriminator"}

    return resolve(schema)


async def _bounded_json(request: Request) -> dict:
    if request.query_params:
        raise HTTPException(
            422, "Use supported fields in the JSON body; query parameters are not accepted"
        )
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Use application/json")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(413, "Request exceeds the demo size limit")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
        if not isinstance(body, dict):
            raise ValueError("Expected object")
        return body
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "Invalid JSON request") from None


def create_app(settings: Settings | None = None, *, agent_model=None) -> FastAPI:
    settings = settings or load_settings()
    comparison_lock = asyncio.Lock()
    comparison_job: asyncio.Task[SpendingComparison] | None = None
    comparison_closing = False
    conversations = ConversationStore()
    context_key = secrets.token_bytes(32)
    receipt_adapter = TypeAdapter(ConversationReceipt)

    def context_fingerprint(label: str, value: str) -> str:
        payload = json.dumps([label, value], ensure_ascii=True, separators=(",", ":")).encode()
        return hmac.new(context_key, payload, hashlib.sha256).hexdigest()

    def context_authority() -> str:
        return context_fingerprint("authority", settings.app_api_token.get_secret_value())

    def context_scope(bound: SpendingTools, source_hash: str, authority: str):
        source = bound.snapshot.source
        if source != settings.data_source:
            return None
        if source == "nessie":
            customer = settings.nessie_customer_id
            selector = settings.nessie_base_url.rstrip("/")
        elif source == "fixture":
            customers = {account.customer_id for account in bound.snapshot.accounts}
            if len(customers) != 1:
                return None
            customer = next(iter(customers))
            selector = str(settings.fixture_path.absolute())
        else:
            return None
        if not isinstance(customer, str) or not customer.strip():
            return None
        return ConversationScope(
            authority=authority,
            source=source,
            selector=context_fingerprint("source-selector", selector),
            customer_id=context_fingerprint("customer", customer),
            source_hash=source_hash,
        )

    async def expire_conversations() -> None:
        while True:
            await asyncio.sleep(CONVERSATION_SWEEP_SECONDS)
            conversations.sweep()

    def comparison_finished(job: asyncio.Task[SpendingComparison]) -> None:
        nonlocal comparison_job, comparison_closing
        if job.cancelled():
            # This owner never cancels a native job. Unexpected task cancellation
            # cannot prove that its thread stopped, so keep admission closed.
            comparison_closing = True
            return
        job.exception()  # Retrieve failures even after the HTTP caller has left.
        if comparison_job is job:
            comparison_job = None

    @asynccontextmanager
    async def lifespan(_app):
        nonlocal comparison_closing
        expiry_job = asyncio.create_task(expire_conversations(), name="conversation-expiry")
        try:
            yield
        finally:
            comparison_closing = True
            conversations.close()
            expiry_job.cancel()
            with suppress(asyncio.CancelledError):
                await expiry_job
            # A source load remains request-owned and cancellable. Wait until it
            # releases admission before reading the retained native-job slot.
            async with comparison_lock:
                pending = comparison_job
            if pending is not None:
                try:
                    await asyncio.shield(pending)
                except Exception:
                    # The done callback has consumed the failure. Cancellation
                    # is deliberately not swallowed or reported as worker exit.
                    pass

    app = FastAPI(title="Spending Companion API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    bearer = HTTPBearer(auto_error=False)
    # A single request per worker prevents an unbounded queue of paid model work.
    agent_lock = asyncio.Lock()
    catalog_lock = asyncio.Lock()
    catalog = (
        CostarCatalog(
            settings.costar_data_dir,
            snapshot_date=settings.costar_snapshot_date.isoformat()
            if settings.costar_snapshot_date
            else None,
        )
        if settings.costar_enabled
        else None
    )

    def authorize(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        expected = settings.app_api_token.get_secret_value()
        if expected and (
            len(request.headers.getlist("authorization")) != 1
            or not credentials
            or not secrets.compare_digest(
                credentials.credentials.encode("utf-8"),
                expected.encode("utf-8"),
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

    @app.get("/api/v1/agent/status", dependencies=[Depends(authorize)])
    def agent_status(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {
            "enabled": settings.llm_enabled,
            "provider": settings.llm_provider,
            "model": settings.gemini_model
            if settings.llm_provider == "gemini"
            else settings.nemotron_model,
            "read_only": True,
            "costar_enabled": settings.costar_enabled,
            "framework": "langchain-deepagents",
            "max_total_output_tokens": settings.llm_max_total_output_tokens,
            "max_model_turns": settings.llm_max_model_turns,
            "max_tool_calls": settings.llm_max_tool_calls,
            "timeout_seconds": settings.llm_timeout_seconds,
        }

    @app.post(
        "/api/v1/agent/answer",
        response_model=AgentAnswer,
        response_model_exclude_none=True,
        dependencies=[Depends(authorize)],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": _inline_request_schema(AgentQuestion)}},
            }
        },
    )
    async def agent_answer(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not settings.llm_enabled:
            raise HTTPException(503, "AI is disabled; enable it in the public settings file")
        try:
            question = AgentQuestion.model_validate(await _bounded_json(request))
        except ValidationError:
            raise HTTPException(
                422, "Provide a question of 1 to 2000 characters and supported conversation fields"
            ) from None
        if agent_lock.locked():
            raise HTTPException(429, "An AI request is already running; retry shortly")
        async with agent_lock:
            lease = None
            scope_unavailable = False
            bound_source_hash = None
            authority = context_authority()
            reference = (
                ConversationReference(question.conversation.id, question.conversation.revision)
                if isinstance(question.conversation, ContinueConversation)
                else None
            )

            def prepare_context(bound: SpendingTools, source_hash: str):
                nonlocal lease, scope_unavailable, bound_source_hash
                bound_source_hash = source_hash
                scope = context_scope(bound, source_hash, authority)
                if scope is None:
                    if reference is not None:
                        conversations.forget(reference.id, authority)
                        raise ConversationResetError()
                    scope_unavailable = True
                    return None
                lease = conversations.begin(question.question, scope, reference)
                return lease.history

            try:
                if reference is not None:
                    conversations.preflight(question.question, reference, authority)
                async with asyncio.timeout(settings.llm_timeout_seconds):
                    tools = await scoped_tools(None)
                    options = {"model": agent_model, "catalog": catalog}
                    if question.conversation is not None:
                        options["prepare_context"] = prepare_context
                    result = await answer_spending(
                        tools,
                        question.question,
                        settings,
                        **options,
                    )
                    if question.conversation is not None:
                        if bound_source_hash is None or result.source_hash != bound_source_hash:
                            raise ValueError("Invalid conversation result scope")
                        if scope_unavailable:
                            receipt = {
                                "state": "closed", "reason": "scope_unavailable", "turns_used": 0
                            }
                        else:
                            try:
                                receipt = conversations.finish(
                                    lease, result.status, result.needed_inputs
                                )
                            except ConversationResetError:
                                raise HTTPException(
                                    503, "AI returned invalid context state"
                                ) from None
                        result.conversation = (
                            receipt_adapter.validate_python(receipt)
                            if receipt is not None
                            else None
                        )
                    return result
            except ConversationResetError as exc:
                raise HTTPException(409, str(exc), headers={"Cache-Control": "no-store"}) from None
            except ConversationBusyError as exc:
                raise HTTPException(429, str(exc), headers={"Cache-Control": "no-store"}) from None
            except TimeoutError:
                raise HTTPException(504, "AI request deadline exceeded") from None
            except (OSError, ValueError, KeyError, TypeError):
                raise HTTPException(
                    503, "Customer data cannot support a grounded AI answer"
                ) from None
            finally:
                if lease is not None:
                    conversations.rollback(lease)

    @app.post(
        "/api/v1/agent/context/clear",
        status_code=204,
        dependencies=[Depends(authorize)],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": ClearConversation.model_json_schema()}},
            }
        },
    )
    async def clear_conversation(request: Request):
        try:
            cleared = ClearConversation.model_validate(await _bounded_json(request))
        except ValidationError:
            raise HTTPException(422, "Provide only a valid conversation ID") from None
        conversations.forget(cleared.id, context_authority())
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    @app.post(
        "/api/v1/costar/search",
        dependencies=[Depends(authorize)],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": CostarSearchCriteria.model_json_schema()}
                },
            }
        },
    )
    async def costar_search(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if catalog is None:
            raise HTTPException(503, "CoStar catalog is disabled in the public settings file")
        try:
            criteria = CostarSearchCriteria.model_validate(await _bounded_json(request))
        except ValidationError:
            raise HTTPException(422, "Invalid catalog search filters") from None
        if catalog_lock.locked():
            raise HTTPException(429, "A catalog search is running; retry shortly")
        async with catalog_lock:
            result = await run_in_threadpool(catalog.search, criteria)
        if result["status"] != "ok":
            response.status_code = 503
        return result

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

    @app.post(
        "/api/v1/spending/compare",
        response_model=SpendingComparison,
        dependencies=[Depends(authorize)],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": _comparison_schema()}},
            }
        },
    )
    async def spending_comparison(request: Request, response: Response):
        nonlocal comparison_job
        response.headers["Cache-Control"] = "no-store"
        if comparison_closing:
            raise HTTPException(503, "Spending comparisons are temporarily unavailable")
        try:
            periods = ComparisonRequest.model_validate(await _bounded_json(request))
        except ValidationError:
            raise HTTPException(
                422, "Provide two ordered, nonoverlapping baseline and current UTC date ranges"
            ) from None
        if comparison_lock.locked() or comparison_job is not None:
            raise HTTPException(429, "A spending comparison is running; retry shortly")
        async with comparison_lock:
            if comparison_closing:
                raise HTTPException(503, "Spending comparisons are temporarily unavailable")
            try:
                async with asyncio.timeout(45):
                    tools = await scoped_tools(None)
                    if comparison_closing:
                        raise HTTPException(503, "Spending comparisons are temporarily unavailable")
                    # Register ownership before yielding. HTTP timeout/cancellation
                    # releases the request lock but not this native-work slot.
                    comparison_job = asyncio.create_task(
                        run_in_threadpool(compare_spending, tools, periods),
                        name="spending-comparison",
                    )
                    comparison_job.add_done_callback(comparison_finished)
                    return await asyncio.shield(comparison_job)
            except TimeoutError:
                raise HTTPException(504, "Spending comparison deadline exceeded") from None
            except (OSError, ValueError, KeyError, TypeError):
                raise HTTPException(503, "Observed data cannot support this comparison") from None

    @app.post("/webhooks/photon", status_code=204, response_class=Response)
    async def photon_webhook(request: Request):
        if not settings.photon_enabled or not all(
            [
                settings.photon_webhook_secret.get_secret_value(),
                settings.photon_project_id,
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
                    accepted_scope_hash=scope_hash(settings),
                )
            except ValueError:
                raise HTTPException(400, "Conflicting Photon event") from None
            except (sqlite3.Error, OSError) as exc:
                raise HTTPException(503, "Unable to persist event; retry later") from exc
        return Response(status_code=204)

    return app
