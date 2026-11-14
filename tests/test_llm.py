"""Credential-free agent tests: real LangChain messages/tools, scripted fake models."""

import asyncio
import copy
import importlib
import json
import socket
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from spending_api.agent_tools import SpendingTools
from spending_api.config import Settings
from spending_api.llm import _read_tools, answer_spending, create_provider_model
from spending_api.models import Account, Snapshot, Transaction


def config(**overrides):
    return Settings(
        _env_file=None,
        **{
            "llm_enabled": True,
            "app_api_token": "synthetic-app-token",
            "gemini_api_key": "synthetic-gemini-token",
            "nvidia_api_key": "synthetic-nvidia-token",
            **overrides,
        },
    )


def snapshot():
    account = Account(
        id="a1", customer_id="c1", nickname="synthetic", type="checking", balance_cents=12_000
    )
    rows = [
        ("purchase", "completed", 2000, "dining"),
        ("refund", "completed", 500, "dining"),
        ("purchase", "pending", 700, "dining"),
        ("transfer", "completed", 900, "other"),
        ("fee", "completed", 100, "fees"),
        ("deposit", "completed", 10_000, "other"),
        ("purchase", "declined", 800, "dining"),
        ("withdrawal", "completed", 300, "other"),
    ]
    return Snapshot(
        source="synthetic-test",
        accounts=[account],
        transactions=[
            Transaction(
                id=f"t{i}",
                account_id="a1",
                kind=kind,
                status=status,
                amount_cents=cents,
                occurred_at=datetime(2026, 11, 1, 12, tzinfo=UTC),
                merchant="Example",
                merchant_id="m1",
                category=category,
                description="PRIVATE UNUSED DESCRIPTION",
            )
            for i, (kind, status, cents, category) in enumerate(rows)
        ],
    )


def call(name="summarize_spending", args=None, call_id="call-1"):
    return {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}


class ScriptedModel:
    def __init__(self, *responses, on_invoke=None):
        self.responses = iter(responses)
        self.messages = []
        self.bound = []
        self.on_invoke = on_invoke

    def bind_tools(self, tools):
        self.bound = tools
        return self

    async def ainvoke(self, messages, config=None):
        assert config == {"callbacks": []}
        self.messages.append(copy.deepcopy(messages))
        if self.on_invoke:
            self.on_invoke()
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def run(model, *, data=None, settings=None, question="Summarize November 1, 2026", **kwargs):
    return asyncio.run(
        answer_spending(
            SpendingTools(data if data is not None else snapshot()),
            question,
            settings if settings is not None else config(),
            model=model,
            **kwargs,
        )
    )


def test_summary_exact_raw_arithmetic_and_evidence_roundtrip():
    model = ScriptedModel(
        AIMessage(content="", tool_calls=[call(args={"start": "2026-11-01", "end": "2026-11-01"})]),
        AIMessage(content="Observed spending was $16.00; snapshot coverage is unknown."),
    )
    answer = run(model)
    assert answer.status == "answered"
    assert (answer.model_calls, answer.tool_calls) == (2, 1)
    evidence = answer.evidence[0]["result"]
    assert evidence["net_spending_cents"] == 2000 - 500 + 100
    assert evidence["included_count"] == 3
    assert evidence["excluded_count"] == 5
    assert evidence["categories"] == [
        {"category": "dining", "amount_cents": 1500},
        {"category": "fees", "amount_cents": 100},
    ]
    message = model.messages[1][-1]
    assert isinstance(message, ToolMessage) and message.tool_call_id == "call-1"
    assert json.loads(message.content) == evidence
    assert len(answer.source_hash) == 64
    assert "unknown" in answer.caveats[0]
    assert "PRIVATE UNUSED DESCRIPTION" not in repr(model.messages)
    assert {tool.name for tool in model.bound} == {
        "summarize_spending",
        "get_transactions",
        "find_recurring_charges",
    }


def test_transaction_signed_evidence_and_truncation():
    model = ScriptedModel(
        AIMessage(
            content="", tool_calls=[call("get_transactions", {"category": "DINING", "limit": 3})]
        ),
        AIMessage(content="The observed dining rows include a purchase and refund."),
    )
    answer = run(model)
    result = answer.evidence[0]["result"]
    assert result["total_rows"] == 4 and result["returned_rows"] == 3 and result["truncated"]
    assert [row["signed_spending_cents"] for row in result["transactions"]] == [2000, -500, 0]
    assert any("subset" in item for item in answer.caveats)
    assert all("account_id" not in row for row in result["transactions"])


def test_utc_boundary_and_defensive_snapshot():
    data = snapshot()
    data.transactions[0].occurred_at = datetime.fromisoformat("2026-10-31T23:30:00-02:00")
    original = data.model_copy(deep=True)
    model = ScriptedModel(
        AIMessage(content="", tool_calls=[call(args={"start": "2026-11-01", "end": "2026-11-01"})]),
        AIMessage(content="Observed $16."),
        on_invoke=lambda: setattr(data.transactions[0], "amount_cents", 999_999),
    )
    answer = run(model, data=data)
    assert answer.evidence[0]["result"]["net_spending_cents"] == 1600
    assert original.transactions[0].occurred_at.utcoffset().total_seconds() == -7200


@pytest.mark.parametrize(
    "args",
    [
        {"customer_id": "other"},
        {"source_path": ".env"},
        {"account_id": "other"},
        {"start": "2026-12-01", "end": "2026-11-01"},
        {"start": 0},
        {"start": "2026-11-01T00:00:00Z"},
        {"start": "2026-02-30"},
    ],
)
def test_bad_tool_arguments_rejected_before_read(args, monkeypatch):
    def fail(*_args):
        pytest.fail("invalid arguments must not execute")

    monkeypatch.setattr(SpendingTools, "summarize_spending", fail)
    answer = run(ScriptedModel(AIMessage(content="", tool_calls=[call(args=args)])))
    assert answer.status == "unavailable" and answer.tool_calls == 0


@pytest.mark.parametrize(
    "bad",
    [
        call("delete_transactions"),
        call(call_id=""),
        call(call_id="x" * 129),
        call(args={"source": "x" * 5000}),
    ],
)
def test_whole_batch_validated_before_any_read(bad, monkeypatch):
    monkeypatch.setattr(SpendingTools, "summarize_spending", lambda *_: pytest.fail("must not run"))
    answer = run(ScriptedModel(AIMessage(content="", tool_calls=[call(call_id="good"), bad])))
    assert answer.status == "unavailable" and answer.tool_calls == 0


def test_duplicate_call_id_rejected_across_turns():
    answer = run(
        ScriptedModel(
            AIMessage(content="", tool_calls=[call()]),
            AIMessage(content="", tool_calls=[call()]),
        )
    )
    assert answer.status == "unavailable" and answer.tool_calls == 1


def test_tool_budget_prevents_oversized_batch():
    answer = run(
        ScriptedModel(AIMessage(content="", tool_calls=[call(), call(call_id="two")])),
        settings=config(llm_max_tool_calls=1),
    )
    assert answer.status == "limited" and answer.tool_calls == 0


def test_model_budget_stops_without_additional_call():
    model = ScriptedModel(AIMessage(content="", tool_calls=[call()]))
    answer = run(model, settings=config(llm_max_model_turns=1))
    assert answer.status == "limited" and answer.model_calls == 1 and answer.tool_calls == 1


def test_missing_tool_evidence_cannot_be_answered():
    answer = run(ScriptedModel(AIMessage(content="You spent $123.45.")))
    assert answer.status == "unavailable" and "$123.45" not in answer.answer


def test_empty_snapshot_only_claims_observed_zero():
    data = snapshot()
    data.transactions = []
    answer = run(
        ScriptedModel(
            AIMessage(content="", tool_calls=[call()]),
            AIMessage(content="Observed records sum to $0."),
        ),
        data=data,
    )
    assert answer.evidence[0]["result"]["net_spending_cents"] == 0
    assert answer.evidence[0]["result"]["coverage"] == "observed_records_only"
    assert "completeness" in " ".join(answer.caveats)


@pytest.mark.parametrize("question", ["", "  ", "x" * 2001, None])
def test_question_bounds(question):
    with pytest.raises(ValueError, match="Question"):
        run(ScriptedModel(), question=question)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_tx",
        "duplicate_account",
        "foreign_account",
        "other_customer",
        "naive_timestamp",
        "blank_id",
    ],
)
def test_invalid_snapshot_never_calls_model(change):
    data = snapshot()
    if change == "duplicate_tx":
        data.transactions.append(data.transactions[0].model_copy())
    elif change == "duplicate_account":
        data.accounts.append(data.accounts[0].model_copy())
    elif change == "foreign_account":
        data.transactions[0].account_id = "foreign"
    elif change == "other_customer":
        data.accounts.append(data.accounts[0].model_copy(update={"id": "a2", "customer_id": "c2"}))
    elif change == "naive_timestamp":
        data.transactions[0].occurred_at = datetime(2026, 11, 1)
    else:
        data.transactions[0].id = ""
    with pytest.raises(ValueError):
        run(ScriptedModel(), data=data)


def test_provider_error_sanitized_and_no_fallback():
    answer = run(ScriptedModel(RuntimeError("secret-token raw prompt filesystem credentials")))
    assert answer.status == "unavailable" and answer.model_calls == 1
    assert "secret-token" not in answer.model_dump_json()


def test_tool_exception_sanitized(monkeypatch):
    def fail(*_):
        raise ValueError("private raw diagnostic")

    monkeypatch.setattr(SpendingTools, "summarize_spending", fail)
    answer = run(
        ScriptedModel(AIMessage(content="", tool_calls=[call()]), AIMessage(content="done"))
    )
    assert answer.status == "unavailable"
    assert answer.evidence[0]["result"]["reason"] == "tool_failed"
    assert "private raw diagnostic" not in answer.model_dump_json()


def test_oversized_tool_payload_withheld():
    data = snapshot()
    data.transactions[0].merchant = "x" * 25_000
    answer = run(
        ScriptedModel(
            AIMessage(content="", tool_calls=[call("get_transactions")]), AIMessage(content="done")
        ),
        data=data,
    )
    assert answer.evidence[0]["result"]["reason"] == "payload_too_large"
    assert answer.status == "unavailable"


def test_reasoning_blocks_not_returned():
    answer = run(
        ScriptedModel(
            AIMessage(content="", tool_calls=[call()]),
            AIMessage(
                content=[
                    {"type": "reasoning", "reasoning": "private analysis"},
                    {"type": "text", "text": "Observed $16."},
                ]
            ),
        )
    )
    assert answer.answer == "Observed $16."


def test_deadline_and_cancellation():
    class Slow(ScriptedModel):
        async def ainvoke(self, *args, **kwargs):
            await asyncio.sleep(5)

    answer = run(Slow(), settings=config(llm_timeout_seconds=1))
    assert answer.status == "limited" and "timed out" in answer.answer


def test_disabled_is_lazy(monkeypatch):
    monkeypatch.setattr("spending_api.llm.create_provider_model", lambda _: pytest.fail("not lazy"))
    answer = run(ScriptedModel(), settings=config(llm_enabled=False))
    assert answer.model_calls == 0 and "disabled" in answer.answer


@pytest.mark.parametrize("provider", ["gemini", "nemotron"])
def test_selected_provider_constructor_arguments(provider, monkeypatch):
    seen = []

    def capture(**kwargs):
        seen.append(kwargs)
        return object()

    module = "langchain_google_genai" if provider == "gemini" else "langchain_nvidia_ai_endpoints"
    name = "ChatGoogleGenerativeAI" if provider == "gemini" else "ChatNVIDIA"
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(**{name: capture}))
    create_provider_model(config(llm_provider=provider))
    assert len(seen) == 1
    args = seen[0]
    assert args["timeout"] == 45
    assert args["api_key"] == f"synthetic-{'gemini' if provider == 'gemini' else 'nvidia'}-token"
    if provider == "gemini":
        assert args["vertexai"] is False and args["max_tokens"] == 1024
        assert args["max_retries"] == 0
    else:
        assert args["base_url"] == "https://integrate.api.nvidia.com/v1"
        assert args["max_completion_tokens"] == 1024
        assert args["usage_telemetry_enabled"] is False


@pytest.mark.parametrize("provider", ["gemini", "nemotron"])
def test_actual_provider_construction_offline(provider, monkeypatch):
    """Actual installed constructors must not discover models or contact a provider."""
    # Import before the socket guard: imports may inspect machine metadata locally.
    importlib.import_module(
        "langchain_google_genai" if provider == "gemini" else "langchain_nvidia_ai_endpoints"
    )

    def no_network(*args, **kwargs):
        raise AssertionError("Provider constructor attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    model = create_provider_model(config(llm_provider=provider))
    assert model is not None
    bound = model.bind_tools(_read_tools(SpendingTools(snapshot()), None))
    assert bound is not None
    if provider == "nemotron":
        assert "timeout" not in model.model_kwargs
        assert model._async_client.timeout == 45
        assert model._client.usage_telemetry_enabled is False


def test_catalog_bound_only_by_server():
    class Catalog:
        def __init__(self):
            self.criteria = []

        def search(self, criteria):
            self.criteria.append(criteria)
            return {
                "status": "ok",
                "source_kind": "costar_local_snapshot",
                "results": [],
                "limitations": ["No live availability or interior observations."],
            }

    catalog = Catalog()
    answer = run(
        ScriptedModel(
            AIMessage(content="", tool_calls=[call("costar_search", {"state": "TX", "limit": 2})]),
            AIMessage(content="No matching recorded listings."),
        ),
        catalog=catalog,
    )
    assert answer.status == "answered" and len(catalog.criteria) == 1
    assert catalog.criteria[0].limit == 2
    assert answer.evidence[0]["result"]["source_kind"] == "costar_local_snapshot"


def test_catalog_tool_unavailable_unless_supplied():
    answer = run(ScriptedModel(AIMessage(content="", tool_calls=[call("costar_search")])))
    assert answer.status == "unavailable" and answer.tool_calls == 0


def test_actual_langchain_fake_model_and_ambient_tracing_disabled(monkeypatch):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langsmith import get_tracing_context

    class ToolFake(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            assert len(tools) == 3
            return self

        async def ainvoke(self, *args, **kwargs):
            assert get_tracing_context()["enabled"] is False
            return await super().ainvoke(*args, **kwargs)

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    model = ToolFake(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[call()]),
                AIMessage(content="Observed $16."),
            ]
        )
    )

    async def invoke_after_event_loop_created():
        # Windows asyncio creates a local socket pair during loop initialization.
        monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("tracing I/O"))
        return await answer_spending(
            SpendingTools(snapshot()), "Summarize observed spending", config(), model=model
        )

    answer = asyncio.run(invoke_after_event_loop_created())
    assert answer.status == "answered" and answer.answer == "Observed $16."


def test_factory_failure_does_not_echo_exception(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("synthetic-key-and-sensitive-provider-diagnostic")

    monkeypatch.setitem(
        sys.modules, "langchain_google_genai", SimpleNamespace(ChatGoogleGenerativeAI=fail)
    )
    with pytest.raises(ValueError, match="could not be initialized") as caught:
        create_provider_model(config())
    assert "sensitive" not in str(caught.value)


def test_invalid_serialized_tool_call_not_accepted():
    answer = run(
        ScriptedModel(
            AIMessage(
                content="unsafe fallback",
                invalid_tool_calls=[
                    {
                        "name": "summarize_spending",
                        "args": "{broken",
                        "id": "bad",
                        "error": "bad JSON",
                    }
                ],
            )
        )
    )
    assert answer.status == "unavailable" and answer.tool_calls == 0


def test_oversized_model_text_not_accepted():
    answer = run(
        ScriptedModel(AIMessage(content="", tool_calls=[call()]), AIMessage(content="x" * 8001))
    )
    assert answer.status == "unavailable" and "x" * 50 not in answer.answer


@pytest.mark.parametrize("limit", [True, "3", 0, 51])
def test_transaction_limit_is_strict(limit):
    answer = run(
        ScriptedModel(
            AIMessage(
                content="",
                tool_calls=[
                    call(
                        "get_transactions",
                        {"limit": limit},
                    )
                ],
            )
        )
    )
    assert answer.status == "unavailable" and answer.tool_calls == 0


def test_recurring_is_only_a_candidate_with_evidence():
    data = snapshot()
    data.transactions = [
        data.transactions[0].model_copy(
            update={
                "id": f"month-{month}",
                "occurred_at": datetime(2026, month, 1, tzinfo=UTC),
            }
        )
        for month in (9, 10, 11)
    ]
    answer = run(
        ScriptedModel(
            AIMessage(content="", tool_calls=[call("find_recurring_charges")]),
            AIMessage(content="One candidate, not a confirmed subscription."),
        ),
        data=data,
    )
    result = answer.evidence[0]["result"]
    assert result["candidates"][0]["transaction_ids"] == ["month-9", "month-10", "month-11"]
    assert result["candidates"][0]["confidence"] == "candidate"
    assert "No confirmed subscriptions" in result["limitations"]


@pytest.mark.parametrize(
    "status,category",
    [
        (401, "authentication_rejected"),
        (403, "authorization_rejected"),
        (400, "invalid_request"),
        (429, "rate_limited"),
        (503, "provider_error"),
        ("private-response-value", "unknown"),
        (True, "unknown"),
    ],
)
def test_smoke_diagnostic_uses_status_without_error_text(status, category):
    from scripts.smoke_ai import DiagnosticModel

    provider = ScriptedModel(Exception("private-token-and-provider-response"))
    provider._async_client = SimpleNamespace(last_response=SimpleNamespace(status=status))
    diagnostic = DiagnosticModel(provider)
    diagnostic.bind_tools([])
    with pytest.raises(Exception):
        asyncio.run(diagnostic.ainvoke([], config={"callbacks": []}))
    assert diagnostic.failure["type"] == "Exception"
    assert diagnostic.failure["category"] == category
    assert diagnostic.failure["http_status"] == (status if type(status) is int else None)
    assert "private" not in json.dumps(diagnostic.failure)


def test_smoke_diagnostic_never_makes_more_than_two_requests():
    from scripts.smoke_ai import DiagnosticModel

    provider = ScriptedModel(AIMessage(content="first"), AIMessage(content="second"))
    diagnostic = DiagnosticModel(provider)
    asyncio.run(diagnostic.ainvoke([], config={"callbacks": []}))
    asyncio.run(diagnostic.ainvoke([], config={"callbacks": []}))
    with pytest.raises(RuntimeError, match="limit"):
        asyncio.run(diagnostic.ainvoke([], config={"callbacks": []}))
    assert len(provider.messages) == 2
    assert diagnostic.failure["category"] == "local_call_limit"


@pytest.mark.parametrize("provider", ["gemini", "nemotron"])
def test_nested_inspection_schema_serializes_for_real_provider_without_network(
    provider, monkeypatch
):
    from langchain_core.messages import HumanMessage

    importlib.import_module(
        "langchain_google_genai" if provider == "gemini" else "langchain_nvidia_ai_endpoints"
    )

    class Catalog:
        def search(self, *_args, **_kwargs):
            pytest.fail("Schema binding must not read catalog data")

    class Inspector:
        async def inspect_floor_plan(self, *_args, **_kwargs):
            pytest.fail("Schema binding must not inspect a model")

    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("unexpected network"))
    monkeypatch.setattr(socket, "create_connection", lambda *_: pytest.fail("unexpected network"))
    model = create_provider_model(config(llm_provider=provider))
    binding = model.bind_tools(_read_tools(SpendingTools(snapshot()), Catalog(), Inspector()))
    # bind_tools uses an intermediate schema. Also exercise the final request
    # conversion, which differs between Google GenAI and NVIDIA integrations.
    messages = [HumanMessage(content="Inspect a selected synthetic floor plan")]
    if provider == "gemini":
        request = model._prepare_request(messages, **binding.kwargs)
        declarations = [
            declaration.model_dump(mode="json", exclude_none=True)
            for tool in request["config"].tools
            for declaration in tool.function_declarations
        ]
        function = next(item for item in declarations if item["name"] == "inspect_floor_plan")
    else:
        _, payload, _ = model._prepare_inputs_and_payload(messages, **binding.kwargs)
        function = next(
            item["function"]
            for item in payload["tools"]
            if item["function"]["name"] == "inspect_floor_plan"
        )
    parameters = function["parameters"]
    assert set(parameters["required"]) == {"criteria", "property_id", "floor_plan_id"}
    assert set(parameters["properties"]) == {
        "criteria",
        "property_id",
        "floor_plan_id",
        "include_dimensions",
    }
    criteria = parameters["properties"]["criteria"]
    assert criteria["type"].lower() == "object"
    assert criteria["properties"]["state"]["enum"] == ["TX", "CA"]
    assert "max_monthly_rent_usd" in criteria["properties"]
    assert "model_id" not in criteria["properties"] and "endpoint" not in criteria["properties"]
    serialized = json.dumps(parameters)
    assert '"$ref"' not in serialized and '"$defs"' not in serialized
    assert "synthetic-gemini-token" not in serialized and "synthetic-nvidia-token" not in serialized


@pytest.mark.parametrize(
    "override",
    [
        {"property_id": True},
        {"property_id": "20"},
        {"floor_plan_id": 0},
        {"include_dimensions": "true"},
        {"model_id": "arbitrary"},
        {"criteria": {"model_id": "arbitrary"}},
        {"criteria": {"limit": 21}},
        {"criteria": {"endpoint": "https://unrelated.invalid"}},
    ],
)
def test_inspection_nested_arguments_rejected_before_any_inspector_read(override):
    class Catalog:
        def search(self, *_args, **_kwargs):
            pytest.fail("Invalid tool input must not read the catalog")

    class Inspector:
        async def inspect_floor_plan(self, *_args, **_kwargs):
            pytest.fail("Invalid tool input must not invoke the inspector")

    arguments = {"criteria": {}, "property_id": 20, "floor_plan_id": 30, **override}
    response = run(
        ScriptedModel(AIMessage(content="", tool_calls=[call("inspect_floor_plan", arguments)])),
        catalog=Catalog(),
        matterport_client=Inspector(),
    )
    assert response.status == "unavailable" and response.tool_calls == 0


def test_inspection_passes_validated_nested_criteria_and_explicit_dimensions_flag():
    from spending_api.costar import CostarSearchCriteria

    class Catalog:
        def search(self, *_args, **_kwargs):
            pytest.fail("The wrapper delegates catalog validation to the inspector")

    class Inspector:
        async def inspect_floor_plan(
            self, criteria, property_id, floor_plan_id, *, include_dimensions
        ):
            assert isinstance(criteria, CostarSearchCriteria)
            assert criteria.state == "CA" and criteria.limit == 2
            assert (property_id, floor_plan_id) == (20, 30)
            assert include_dimensions is False
            return {
                "status": "ok",
                "source_kind": "synthetic_inspector",
                "geometry_clearance_verified": False,
                "rooms": [],
            }

    arguments = {
        "criteria": {"state": "CA", "limit": 2},
        "property_id": 20,
        "floor_plan_id": 30,
        "include_dimensions": False,
    }
    response = run(
        ScriptedModel(
            AIMessage(content="", tool_calls=[call("inspect_floor_plan", arguments)]),
            AIMessage(content="No dimensions requested; furniture clearance is unknown."),
        ),
        catalog=Catalog(),
        matterport_client=Inspector(),
    )
    assert response.status == "answered" and response.tool_calls == 1
    assert response.evidence[0]["arguments"] == arguments
    assert response.evidence[0]["result"]["geometry_clearance_verified"] is False


def test_inspection_tool_not_exposed_without_both_catalog_and_inspector():
    def names(tools):
        return {tool.name for tool in tools}

    assert "inspect_floor_plan" not in names(_read_tools(SpendingTools(snapshot()), None, object()))
    assert "inspect_floor_plan" not in names(_read_tools(SpendingTools(snapshot()), object(), None))


@pytest.mark.parametrize(
    "room_details_truncated,reference_count", [(True, 3), (True, 1), (False, 3), (False, 1)]
)
def test_inspection_coverage_limitations_reach_agent_caveats(
    room_details_truncated, reference_count
):
    class Catalog:
        def search(self, *_args, **_kwargs):
            pytest.fail("This wrapper delegates catalog selection to the inspector")

    evidence = {
        "status": "ok",
        "source_kind": "synthetic_inspector",
        "room_count": 25 if room_details_truncated else 5,
        "rooms_returned": 20 if room_details_truncated else 5,
        "rooms_truncated": room_details_truncated,
        "verified_reference_count": reference_count,
        "models_inspected": 1,
        "all_referenced_models_inspected": reference_count == 1,
        "geometry_clearance_verified": False,
    }

    class Inspector:
        async def inspect_floor_plan(self, *_args, **_kwargs):
            return copy.deepcopy(evidence)

    response = run(
        ScriptedModel(
            AIMessage(
                content="",
                tool_calls=[
                    call(
                        "inspect_floor_plan",
                        {
                            "criteria": {},
                            "property_id": 20,
                            "floor_plan_id": 30,
                        },
                    )
                ],
            ),
            AIMessage(content="The selected model has room metadata; clearance remains unknown."),
        ),
        catalog=Catalog(),
        matterport_client=Inspector(),
    )
    assert response.status == "answered"
    assert response.evidence[0]["result"] == evidence
    assert any("bounded subset" in caveat for caveat in response.caveats) is room_details_truncated
    fraction = (
        "inspect_floor_plan inspected 1 of 3 catalog model references; "
        "other models remain uninspected."
    )
    assert (fraction in response.caveats) is (reference_count == 3)
