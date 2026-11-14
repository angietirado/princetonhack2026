"""Offline ownership regressions; fake graphs isolate lifecycle from SDK I/O."""

import asyncio
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from scripts import smoke_ai
from spending_api import llm, provider_lifecycle
from spending_api.agent_tools import SpendingTools
from spending_api.config import Settings
from spending_api.models import Account, DateRange, Snapshot, Transaction

SENSITIVE = "synthetic-secret-do-not-expose"
QUESTION = "Summarize November spending."


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "data_source": "fixture",
            "costar_enabled": False,
            "llm_enabled": True,
            "app_api_token": "synthetic-app-token",
            "gemini_api_key": "synthetic-gemini-key",
            "nvidia_api_key": "synthetic-nvidia-key",
            **overrides,
        },
    )


def spending_tools():
    return SpendingTools(
        Snapshot(
            source="synthetic-cleanup-test",
            accounts=[
                Account(
                    id="a", customer_id="c", nickname="Test", type="Checking", balance_cents=2000
                )
            ],
            transactions=[
                Transaction(
                    id="t",
                    account_id="a",
                    kind="purchase",
                    status="completed",
                    amount_cents=1234,
                    occurred_at=datetime(2026, 11, 5, tzinfo=UTC),
                    merchant="Synthetic Cafe",
                    merchant_id="m",
                    category="dining",
                )
            ],
        )
    )


class OwnedRoot:
    def __init__(self, *, close_error=None, gate=None, events=None, name="root"):
        self.created_loop = asyncio.get_running_loop()
        self.created_task = asyncio.current_task()
        self.close_error = close_error
        self.gate = gate
        self.events = events if events is not None else []
        self.name = name
        self.close_started = asyncio.Event()
        self.close_finished = asyncio.Event()
        self.close_attempts = 0
        self.close_cancelled = False
        self.close_completed = False
        self.views = []

    def bind_tools(self, _tools):
        view = BorrowedModel(provider_model=self)
        self.views.append(view)
        return view

    async def aclose(self):
        self.close_attempts += 1
        assert asyncio.get_running_loop() is self.created_loop
        assert asyncio.current_task() is self.created_task
        self.events.append(f"close-start:{self.name}")
        self.close_started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.close_error is not None:
                raise self.close_error
            self.close_completed = True
        except asyncio.CancelledError:
            self.close_cancelled = True
            raise
        finally:
            self.events.append(f"close-finish:{self.name}")
            self.close_finished.set()


class BorrowedModel:
    def __init__(self, *, falsey=False, provider_model=None):
        self.falsey = falsey
        self.provider_model = provider_model
        self.cleanup_reads = 0

    def __bool__(self):
        return not self.falsey

    @property
    def aclose(self):
        self.cleanup_reads += 1
        raise AssertionError("Borrowed cleanup capability must not be inspected")


def install_graph(monkeypatch, outcome="answered", *, entered=None, bind_views=False):
    def build(model, bound, catalog, budget):
        assert catalog is None
        if outcome == "setup_error":
            raise RuntimeError(SENSITIVE)
        if bind_views:
            model.bind_tools(["advisor"])
            model.bind_tools(["specialist"])

        async def invoke(_messages, *, config):
            assert config == {"callbacks": [], "recursion_limit": 32}
            budget.model_calls = 2
            budget.tool_calls = 1
            budget.output_tokens_reserved = 2048
            budget.output_tokens_reported = 23
            if entered is not None:
                entered.set()
            if outcome == "wait":
                await asyncio.Event().wait()
            if outcome == "error":
                raise RuntimeError(SENSITIVE)
            if outcome == "limit":
                raise llm._LimitReached
            if outcome == "clarification":
                budget.needed_inputs = ["date_range"]
            if outcome == "answered":
                summary = bound.summarize_spending(
                    DateRange(start=date(2026, 11, 1), end=date(2026, 11, 13))
                )
                budget.evidence.append(
                    {
                        "tool": "summarize_spending",
                        "result": {"status": "ok", **summary.model_dump(mode="json")},
                    }
                )
            return {"messages": [AIMessage(content="Observed spending is $12.34.")]}

        return SimpleNamespace(ainvoke=invoke)

    monkeypatch.setattr(llm, "_build_advisor", build)


def install_factory(monkeypatch, **root_options):
    roots = []

    def create(_settings):
        root = OwnedRoot(**root_options)
        roots.append(root)
        return root

    monkeypatch.setattr(llm, "create_provider_model", create)
    return roots


def assert_answer(result):
    assert result.status == "answered"
    assert result.answer == "Observed spending is $12.34."
    assert result.source == "synthetic-cleanup-test"
    assert len(result.source_hash) == 64
    assert result.model_calls == 2
    assert result.tool_calls == 1
    assert result.output_tokens_reserved == 2048
    assert result.output_tokens_reported == 23
    assert len(result.evidence) == 1
    assert result.evidence[0]["result"]["net_spending_cents"] == 1234


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        ("answered", "answered"),
        ("clarification", "clarification"),
        ("no_evidence", "unavailable"),
        ("setup_error", "unavailable"),
        ("error", "unavailable"),
        ("limit", "limited"),
    ],
)
def test_owned_root_closes_once_and_preserves_primary_outcome(
    monkeypatch, outcome, expected_status, capsys, caplog
):
    install_graph(monkeypatch, outcome)
    roots = install_factory(monkeypatch)
    result = asyncio.run(llm.answer_spending(spending_tools(), QUESTION, settings()))
    assert result.status == expected_status
    assert len(roots) == 1
    assert roots[0].close_attempts == 1
    assert roots[0].close_completed
    if outcome == "answered":
        assert_answer(result)
    if outcome == "clarification":
        assert result.answer == "Please provide: date range."
        assert result.needed_inputs == ["date_range"]
    assert SENSITIVE not in result.model_dump_json() + str(capsys.readouterr()) + caplog.text


def test_owned_root_closes_after_primary_timeout_has_unwound(monkeypatch):
    install_graph(monkeypatch, "wait")
    roots = install_factory(monkeypatch)
    short = settings().model_copy(update={"llm_timeout_seconds": 0.01})
    result = asyncio.run(llm.answer_spending(spending_tools(), QUESTION, short))
    assert result.status == "limited"
    assert "timed out" in result.answer
    assert roots[0].close_attempts == 1
    assert roots[0].close_completed
    assert not roots[0].close_cancelled


def test_owned_root_closes_before_caller_cancellation_propagates(monkeypatch):
    async def check():
        entered = asyncio.Event()
        install_graph(monkeypatch, "wait", entered=entered)
        roots = install_factory(monkeypatch)
        task = asyncio.create_task(llm.answer_spending(spending_tools(), QUESTION, settings()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert roots[0].close_attempts == 1
            assert roots[0].close_completed
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.parametrize("early_exit", ["question", "snapshot", "disabled", "factory"])
def test_no_cleanup_owner_is_invented_before_factory_return(monkeypatch, early_exit):
    calls = []

    def factory(_settings):
        calls.append("factory")
        raise RuntimeError(SENSITIVE)

    async def unexpected_cleanup(_model):
        pytest.fail("No root was acquired")

    monkeypatch.setattr(llm, "create_provider_model", factory)
    monkeypatch.setattr(llm, "close_owned_provider", unexpected_cleanup)
    tools = spending_tools()
    question = "" if early_exit == "question" else QUESTION
    if early_exit == "snapshot":
        def invalid_snapshot(_tools):
            raise ValueError("Synthetic invalid snapshot")

        monkeypatch.setattr(llm, "_bound_tools", invalid_snapshot)
    config = settings(llm_enabled=early_exit != "disabled")
    if early_exit in {"question", "snapshot"}:
        with pytest.raises(ValueError):
            asyncio.run(llm.answer_spending(tools, question, config))
    else:
        result = asyncio.run(llm.answer_spending(tools, question, config))
        assert result.status == "unavailable"
        assert SENSITIVE not in result.model_dump_json()
    assert calls == (["factory"] if early_exit == "factory" else [])


@pytest.mark.parametrize("kind", ["native", "falsey", "binding", "diagnostic"])
@pytest.mark.parametrize("outcome", ["answered", "error", "wait", "cancel"])
def test_injected_models_are_borrowed_on_every_exit(monkeypatch, kind, outcome):
    async def check():
        root = BorrowedModel(falsey=kind == "falsey")
        model = root
        if kind == "binding":
            model = BorrowedModel(provider_model=root)
        elif kind == "diagnostic":
            model = smoke_ai.DiagnosticModel(root)
        entered = asyncio.Event()
        install_graph(monkeypatch, "wait" if outcome == "cancel" else outcome, entered=entered)
        monkeypatch.setattr(
            llm, "create_provider_model", lambda _: pytest.fail("Injected model is borrowed")
        )
        config = settings().model_copy(
            update={"llm_timeout_seconds": 0.01 if outcome == "wait" else 2}
        )
        task = asyncio.create_task(
            llm.answer_spending(spending_tools(), QUESTION, config, model=model)
        )
        try:
            if outcome == "cancel":
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await asyncio.wait_for(task, 2)
                assert result.status == {
                    "answered": "answered", "error": "unavailable", "wait": "limited"
                }[outcome]
            assert root.cleanup_reads == 0
            if isinstance(model, BorrowedModel):
                assert model.cleanup_reads == 0
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


def test_owned_root_is_closed_without_unwrapping_bound_views(monkeypatch):
    install_graph(monkeypatch, bind_views=True)
    roots = install_factory(monkeypatch)
    result = asyncio.run(llm.answer_spending(spending_tools(), QUESTION, settings()))
    assert_answer(result)
    assert len(roots[0].views) == 2
    assert roots[0].close_attempts == 1
    assert all(view.cleanup_reads == 0 for view in roots[0].views)


@pytest.mark.parametrize("close_error", [RuntimeError(SENSITIVE), TimeoutError(SENSITIVE)])
@pytest.mark.parametrize("outcome", ["answered", "error"])
def test_cleanup_errors_do_not_replace_or_disclose_primary_result(
    monkeypatch, close_error, outcome, capsys, caplog
):
    install_graph(monkeypatch, outcome)
    roots = install_factory(monkeypatch, close_error=close_error)
    result = asyncio.run(llm.answer_spending(spending_tools(), QUESTION, settings()))
    assert roots[0].close_attempts == 1
    assert not roots[0].close_completed
    if outcome == "answered":
        assert_answer(result)
    else:
        assert result.status == "unavailable"
    assert SENSITIVE not in result.model_dump_json() + str(capsys.readouterr()) + caplog.text


@pytest.mark.parametrize("exit_mode", ["released", "budget", "new_cancel", "second_cancel"])
def test_cleanup_is_awaited_in_owner_task_without_detached_work(monkeypatch, exit_mode):
    async def check():
        gate = asyncio.Event()
        entered = asyncio.Event()
        install_graph(
            monkeypatch, "wait" if exit_mode == "second_cancel" else "answered", entered=entered
        )
        roots = install_factory(monkeypatch, gate=gate)
        monkeypatch.setattr(
            provider_lifecycle,
            "PROVIDER_CLEANUP_TIMEOUT_SECONDS",
            0.02 if exit_mode == "budget" else 2,
        )
        before = asyncio.all_tasks()
        task = asyncio.create_task(llm.answer_spending(spending_tools(), QUESTION, settings()))
        try:
            # The factory runs in the owner task, not in this test's task.
            for _ in range(20):
                if roots:
                    break
                await asyncio.sleep(0)
            assert len(roots) == 1
            root = roots[0]
            if exit_mode == "second_cancel":
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
            await asyncio.wait_for(root.close_started.wait(), 2)
            assert not task.done()
            if exit_mode in {"new_cancel", "second_cancel"}:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                if exit_mode == "released":
                    gate.set()
                result = await asyncio.wait_for(task, 2)
                assert_answer(result)
            assert root.close_attempts == 1
            assert root.close_finished.is_set()
            assert root.close_completed == (exit_mode == "released")
            assert root.close_cancelled == (exit_mode != "released")
            assert asyncio.all_tasks() == before
        finally:
            gate.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.parametrize("capability", ["absent", "noncallable", "sync", "descriptor_error"])
def test_cleanup_only_uses_supported_public_async_capability(
    monkeypatch, capability, capsys, caplog
):
    calls = []

    class Provider:
        def close(self):
            pytest.fail("No synchronous fallback")

        @property
        def _client(self):
            pytest.fail("No private transport inspection")

    root = Provider()
    if capability == "noncallable":
        root.aclose = 42
    elif capability == "sync":
        root.aclose = lambda: calls.append("sync-close")
    elif capability == "descriptor_error":
        def raising_property(_self):
            raise RuntimeError(SENSITIVE)

        monkeypatch.setattr(Provider, "aclose", property(raising_property), raising=False)
    install_graph(monkeypatch)
    monkeypatch.setattr(llm, "create_provider_model", lambda _: root)
    result = asyncio.run(llm.answer_spending(spending_tools(), QUESTION, settings()))
    assert_answer(result)
    assert calls == []
    assert SENSITIVE not in result.model_dump_json() + str(capsys.readouterr()) + caplog.text


def test_async_callable_close_capability_is_awaited_once(monkeypatch):
    calls = []

    class AsyncCloser:
        async def __call__(self):
            await asyncio.sleep(0)
            calls.append(asyncio.current_task())

    async def check():
        root = SimpleNamespace(aclose=AsyncCloser())
        install_graph(monkeypatch)
        monkeypatch.setattr(llm, "create_provider_model", lambda _: root)
        result = await llm.answer_spending(spending_tools(), QUESTION, settings())
        assert_answer(result)
        assert calls == [asyncio.current_task()]

    asyncio.run(check())


def smoke_args(tmp_path, *, provider="gemini"):
    config = tmp_path / "synthetic-public-settings.toml"
    config.write_text('data_source = "fixture"\n', encoding="utf-8")
    return SimpleNamespace(settings=config, env_file=None, provider=provider)


def install_smoke_settings(monkeypatch, *, key_present=True):
    def synthetic_settings(**options):
        assert options.pop("_env_file") is None
        # Explicit synthetic settings: no secret file or live provider is involved.
        return settings(
            **options,
            gemini_api_key="synthetic-key" if key_present else "",
            nvidia_api_key="synthetic-key" if key_present else "",
        )

    monkeypatch.setattr(smoke_ai, "Settings", synthetic_settings)


def test_smoke_owns_roots_and_finishes_each_before_creating_next(monkeypatch, tmp_path, capsys):
    install_smoke_settings(monkeypatch)
    install_graph(monkeypatch, bind_views=True)
    events, roots = [], []

    def factory(config):
        events.append(f"factory:{config.llm_provider}")
        root = OwnedRoot(events=events, name=config.llm_provider)
        roots.append(root)
        return root

    monkeypatch.setattr(smoke_ai, "create_provider_model", factory)
    monkeypatch.setattr(llm, "create_provider_model", lambda _: pytest.fail("Borrowed wrapper"))
    code = asyncio.run(smoke_ai.run(smoke_args(tmp_path, provider="all")))
    assert code == 0
    assert events == [
        "factory:gemini", "close-start:gemini", "close-finish:gemini",
        "factory:nemotron", "close-start:nemotron", "close-finish:nemotron",
    ]
    assert all(root.close_attempts == 1 and len(root.views) == 2 for root in roots)
    assert all(view.cleanup_reads == 0 for root in roots for view in root.views)
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    passed = [row for row in rows if row.get("passed")]
    assert len(passed) == 2
    assert all(row["model_calls"] == 2 and row["tool_calls"] == 1 for row in passed)


@pytest.mark.parametrize("failure", ["wrapper", "answer", "timeout", "cancel"])
def test_smoke_closes_acquired_root_on_every_failure(monkeypatch, tmp_path, failure, capsys):
    install_smoke_settings(monkeypatch)
    roots = []

    def factory(_config):
        root = OwnedRoot()
        roots.append(root)
        return root

    def broken_wrapper(_root):
        raise RuntimeError(SENSITIVE)

    async def broken_answer(*_args, **_kwargs):
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "timeout":
            raise TimeoutError(SENSITIVE)
        raise RuntimeError(SENSITIVE)

    monkeypatch.setattr(smoke_ai, "create_provider_model", factory)
    monkeypatch.setattr(smoke_ai, "answer_spending", broken_answer)
    if failure == "wrapper":
        monkeypatch.setattr(smoke_ai, "DiagnosticModel", broken_wrapper)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(smoke_ai.run(smoke_args(tmp_path, provider="all")))
    else:
        assert asyncio.run(smoke_ai.run(smoke_args(tmp_path))) == 1
    assert len(roots) == 1
    assert roots[0].close_attempts == 1
    assert roots[0].close_completed
    assert SENSITIVE not in str(capsys.readouterr())


@pytest.mark.parametrize("failure", ["error", "budget"])
def test_smoke_cleanup_failure_preserves_success_without_detached_work(
    monkeypatch, tmp_path, failure, capsys, caplog
):
    install_smoke_settings(monkeypatch)
    install_graph(monkeypatch)
    roots, events = [], []
    monkeypatch.setattr(provider_lifecycle, "PROVIDER_CLEANUP_TIMEOUT_SECONDS", 0.01)

    def factory(config):
        if roots:
            assert roots[-1].close_finished.is_set()
        events.append(f"factory:{config.llm_provider}")
        root = OwnedRoot(
            close_error=RuntimeError(SENSITIVE) if failure == "error" else None,
            gate=asyncio.Event() if failure == "budget" else None,
            events=events,
            name=config.llm_provider,
        )
        roots.append(root)
        return root

    monkeypatch.setattr(smoke_ai, "create_provider_model", factory)

    async def check():
        before = asyncio.all_tasks()
        assert await asyncio.wait_for(smoke_ai.run(smoke_args(tmp_path, provider="all")), 2) == 0
        assert asyncio.all_tasks() == before

    asyncio.run(check())
    assert len(roots) == 2
    assert all(root.close_attempts == 1 and root.close_finished.is_set() for root in roots)
    assert all(root.close_cancelled == (failure == "budget") for root in roots)
    assert SENSITIVE not in str(capsys.readouterr()) + caplog.text


@pytest.mark.parametrize("failure", ["missing_key", "factory"])
def test_smoke_does_not_close_when_no_root_was_acquired(monkeypatch, tmp_path, failure, capsys):
    install_smoke_settings(monkeypatch, key_present=failure != "missing_key")
    calls = []

    def factory(_config):
        calls.append("factory")
        raise RuntimeError(SENSITIVE)

    async def unexpected_cleanup(_model):
        pytest.fail("No root was acquired")

    monkeypatch.setattr(smoke_ai, "create_provider_model", factory)
    monkeypatch.setattr(smoke_ai, "close_owned_provider", unexpected_cleanup)
    assert asyncio.run(smoke_ai.run(smoke_args(tmp_path))) == 1
    assert calls == (["factory"] if failure == "factory" else [])
    assert SENSITIVE not in str(capsys.readouterr())
