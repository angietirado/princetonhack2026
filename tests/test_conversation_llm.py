"""Fresh grounding and user-role context through real graphs and scripted models."""

import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from spending_api import llm
from spending_api.agent_tools import SpendingTools, bind_snapshot
from spending_api.config import Settings
from spending_api.conversation import ConversationResetError, QuestionContext
from spending_api.models import Account, Snapshot, Transaction


def synthetic_snapshot(*, source="fixture", customer="synthetic-customer", empty=False):
    return Snapshot(
        source=source,
        accounts=[] if empty else [Account(
            id="synthetic-account", customer_id=customer, nickname="Synthetic",
            type="checking", balance_cents=5000,
        )],
        transactions=[] if empty else [Transaction(
            id="synthetic-transaction", account_id="synthetic-account", kind="purchase",
            status="completed", amount_cents=1250, occurred_at=datetime(2026, 11, 1, tzinfo=UTC),
            merchant="Synthetic merchant", merchant_id="synthetic-merchant", category="dining",
        )],
    )


def settings():
    return Settings(
        _env_file=None, llm_enabled=True, app_api_token="synthetic-app-token",
        gemini_api_key="synthetic-provider-key",
    )


def tool_call(name, arguments, identifier):
    return {"name": name, "args": arguments, "id": identifier, "type": "tool_call"}


class ConversationModel:
    def __init__(self, mode="answered"):
        self.mode = mode
        self.initial_messages = []
        self.worker_messages = []
        self.calls = 0
        self.answer_text = "CURRENT_ASSISTANT_SENTINEL"

    def bind_tools(self, tools, **kwargs):
        target = self.advisor if "task" in {tool.name for tool in tools} else self.worker
        return SimpleNamespace(ainvoke=target)

    async def advisor(self, messages, config=None):
        self.calls += 1
        if messages[-1].type == "human":
            self.initial_messages.append(copy.deepcopy(messages))
            if self.mode == "clarification":
                return AIMessage(content="", tool_calls=[tool_call(
                    "request_clarification",
                    {"question": "MODEL_CLAIM_$999 Which dates?", "needed_inputs": ["date_range"]},
                    "clarify-current",
                )])
            if self.mode == "bare":
                return AIMessage(content="A remembered claim is sufficient without a new read.")
            if self.mode == "fail":
                raise RuntimeError("synthetic provider failure")
            return AIMessage(content="", tool_calls=[tool_call(
                "task",
                {"subagent_type": "spending", "description": "Summarize November 1, 2026 in UTC."},
                "delegate-current",
            )])
        return AIMessage(content=self.answer_text)

    async def worker(self, messages, config=None):
        self.calls += 1
        self.worker_messages.append(copy.deepcopy(messages))
        if messages[-1].type == "tool":
            return AIMessage(content="CURRENT_SPECIALIST_SENTINEL")
        return AIMessage(content="", tool_calls=[tool_call(
            "summarize_spending", {"start": "2026-11-01", "end": "2026-11-01"}, "sum-current",
        )])


def run(model, question="Summarize November 1, 2026.", **kwargs):
    return asyncio.run(llm.answer_spending(
        SpendingTools(synthetic_snapshot()), question, settings(), model=model, **kwargs,
    ))


def context_payload(messages, prior_question):
    matching = []
    for message in messages:
        if message.type != "human" or not isinstance(message.content, str):
            continue
        text = message.content
        if "{" not in text:
            continue
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
        except ValueError:
            continue
        if isinstance(payload, dict) and prior_question in payload.get("questions", []):
            matching.append(payload)
    assert len(matching) == 1
    return matching[0]


def test_context_preparation_sees_the_single_bound_copy_before_tool_execution(monkeypatch):
    tools = SpendingTools(synthetic_snapshot())
    expected_hash = bind_snapshot(tools)[1]
    actual_bind = llm.bind_snapshot
    bindings, preparations = [], []

    def counted_bind(value):
        bindings.append(value)
        return actual_bind(value)

    def prepare(bound, source_hash):
        preparations.append((bound, source_hash))
        assert bound is not tools and bound.snapshot is not tools.snapshot
        assert source_hash == expected_hash
        tools.snapshot.transactions[0].amount_cents = 999999
        return QuestionContext(questions=("Original period question",), needed_inputs=())

    monkeypatch.setattr(llm, "bind_snapshot", counted_bind)
    model = ConversationModel()
    answer = asyncio.run(llm.answer_spending(
        tools, "Continue that period.", settings(), model=model, prepare_context=prepare,
    ))
    assert len(bindings) == len(preparations) == 1
    assert answer.status == "answered" and answer.source_hash == expected_hash
    assert answer.evidence[0]["result"]["net_spending_cents"] == 1250
    assert answer.evidence[0]["source_hash"] == expected_hash


def test_prepare_reset_propagates_before_budget_or_provider_initialization(monkeypatch):
    seen = []

    def prepare(bound, source_hash):
        seen.append(source_hash)
        raise ConversationResetError()

    def forbidden(*args, **kwargs):
        pytest.fail("a rejected continuation initialized request budget or provider")

    monkeypatch.setattr(llm, "_Budget", forbidden)
    monkeypatch.setattr(llm, "create_provider_model", forbidden)
    with pytest.raises(ConversationResetError):
        asyncio.run(llm.answer_spending(
            SpendingTools(synthetic_snapshot()), "Continue", settings(), prepare_context=prepare,
        ))
    assert len(seen) == 1


def test_history_is_delimited_user_json_with_a_separate_exact_current_question():
    prior = 'Original goal with a "quoted" detail.'
    current = "Use November 1 through 7, 2026."
    history = QuestionContext(questions=(prior,), needed_inputs=("date_range",))
    model = ConversationModel()
    answer = run(model, current, prepare_context=lambda *_: history)
    messages = model.initial_messages[0]
    assert messages[-1].type == "human" and messages[-1].content == current
    assert context_payload(messages, prior) == {
        "questions": [prior], "needed_inputs": ["date_range"],
    }
    assert all(
        prior not in str(message.content) for message in messages if message.type == "system"
    )
    assert not any(message.type in {"ai", "tool"} for message in messages)
    assert answer.status == "answered"


@pytest.mark.parametrize("preparation", ["omitted", "none", "empty"])
def test_legacy_and_empty_context_keep_the_original_single_user_message(preparation):
    model = ConversationModel()
    kwargs = {}
    if preparation == "none":
        kwargs["prepare_context"] = lambda *_: None
    elif preparation == "empty":
        kwargs["prepare_context"] = lambda *_: QuestionContext(questions=(), needed_inputs=())
    answer = run(model, **kwargs)
    users = [message.content for message in model.initial_messages[0] if message.type == "human"]
    assert users == ["Summarize November 1, 2026."]
    assert answer.status == "answered"


def test_new_turn_does_not_replay_previous_assistant_tools_or_usage():
    model = ConversationModel()
    model.answer_text = "OLD_ASSISTANT_MUST_NOT_BECOME_CONTEXT"
    first = run(model, "Original goal")
    assert first.status == "answered" and first.evidence
    model.answer_text = "Current answer"
    history = QuestionContext(questions=("Original goal",), needed_inputs=())
    second = run(model, "Follow-up", prepare_context=lambda *_: history)
    messages = model.initial_messages[1]
    assert "OLD_ASSISTANT_MUST_NOT_BECOME_CONTEXT" not in repr(messages)
    assert "CURRENT_SPECIALIST_SENTINEL" not in repr(messages)
    assert "net_spending_cents" not in repr(messages)
    assert not any(message.type in {"ai", "tool"} for message in messages)
    assert len(second.evidence) == len(first.evidence) == 1
    assert second.model_calls == first.model_calls == 4
    assert second.tool_calls == first.tool_calls == 2


def test_prior_success_cannot_make_a_no_tool_follow_up_answered():
    model = ConversationModel()
    assert run(model, "Original goal").status == "answered"
    model.mode = "bare"
    history = QuestionContext(questions=("Original goal",), needed_inputs=())
    answer = run(model, "Continue", prepare_context=lambda *_: history)
    assert answer.status != "answered" and answer.evidence == []
    assert answer.tool_calls == 0 and answer.model_calls == 1


def test_clarification_keeps_validated_codes_without_model_written_factual_claims():
    model = ConversationModel(mode="clarification")
    history = QuestionContext(questions=("Compare spending",), needed_inputs=("date_range",))
    answer = run(model, "Yes", prepare_context=lambda *_: history)
    assert answer.status == "clarification" and answer.needed_inputs == ["date_range"]
    assert answer.evidence == [] and model.worker_messages == []
    assert "MODEL_CLAIM_$999" not in answer.answer
    assert context_payload(model.initial_messages[0], "Compare spending") == {
        "questions": ["Compare spending"], "needed_inputs": ["date_range"],
    }
