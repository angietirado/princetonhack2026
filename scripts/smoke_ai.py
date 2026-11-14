"""Explicit opt-in provider smoke: synthetic data only, never print keys or provider errors."""

import argparse
import asyncio
import json
import time
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import SecretStr

from spending_api.agent_tools import SpendingTools
from spending_api.config import PublicSettings, Settings
from spending_api.llm import answer_spending, create_provider_model
from spending_api.models import Account, DateRange, Snapshot, Transaction
from spending_api.provider_lifecycle import close_owned_provider


class DiagnosticModel:
    def __init__(self, model, *, _state=None, _provider_model=None):
        self.model = model
        self.provider_model = _provider_model if _provider_model is not None else model
        self._state = _state if _state is not None else {"failure": None, "calls": 0}

    @property
    def failure(self):
        return self._state["failure"]

    @failure.setter
    def failure(self, value):
        self._state["failure"] = value

    @property
    def calls(self):
        return self._state["calls"]

    @calls.setter
    def calls(self, value):
        self._state["calls"] = value

    def bind_tools(self, tools):
        return DiagnosticModel(
            self.model.bind_tools(tools), _state=self._state, _provider_model=self.provider_model
        )

    async def ainvoke(self, messages, config=None):
        # Four shared requests cover advisor delegation, a specialist tool round
        # trip, and the advisor's final response. Never let this
        # diagnostic wrapper silently spend further calls on a recovery loop.
        if self.calls >= 4:
            self.failure = {
                "type": "LocalCallLimit",
                "http_status": None,
                "category": "local_call_limit",
            }
            raise RuntimeError("Smoke model-call limit reached")
        self.calls += 1
        try:
            return await self.model.ainvoke(messages, config=config)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is None:
                status = getattr(exc, "code", None)
            if type(status) is not int:
                # ChatNVIDIA 1.5 wraps aiohttp HTTP failures in a generic
                # Exception. Inspect only the cached response's integer status;
                # its body/headers and the exception text may contain secrets.
                client = getattr(self.provider_model, "_async_client", None)
                response = getattr(client, "last_response", None)
                status = getattr(response, "status", None)
            status = status if type(status) is int and 100 <= status <= 599 else None
            category = {
                400: "invalid_request",
                401: "authentication_rejected",
                403: "authorization_rejected",
                404: "model_or_endpoint_unavailable",
                410: "model_retired",
                422: "invalid_request",
                429: "rate_limited",
            }.get(status, "provider_error" if status and status >= 500 else "unknown")
            self.failure = {
                "type": type(exc).__name__,
                "http_status": status,
                "category": category,
            }
            raise


async def run(args):
    public = PublicSettings.model_validate(tomllib.loads(args.settings.read_text(encoding="utf-8")))
    options = public.model_dump()
    options.update(data_source="fixture", llm_enabled=False, costar_enabled=False)
    # This read is allowed only for the explicitly requested --live smoke.
    settings = Settings(_env_file=args.env_file, **options)
    print(
        json.dumps(
            {
                "synthetic_only": True,
                "app_token_configured": bool(settings.app_api_token.get_secret_value()),
            }
        ),
        flush=True,
    )
    providers = ("gemini", "nemotron") if args.provider == "all" else (args.provider,)
    tools = SpendingTools(
        Snapshot(
            source="synthetic_smoke",
            accounts=[
                Account(
                    id="smoke-account",
                    customer_id="smoke-customer",
                    nickname="Test",
                    type="Checking",
                    balance_cents=10000,
                )
            ],
            transactions=[
                Transaction(
                    id="smoke-purchase",
                    account_id="smoke-account",
                    kind="purchase",
                    status="completed",
                    amount_cents=1234,
                    occurred_at=datetime(2026, 11, 5, tzinfo=UTC),
                    merchant="Synthetic Cafe",
                    merchant_id="smoke-merchant",
                    category="dining",
                )
            ],
        )
    )
    expected = tools.summarize_spending(DateRange(start=date(2026, 11, 1), end=date(2026, 11, 13)))
    successes = []
    for provider in providers:
        key = settings.gemini_api_key if provider == "gemini" else settings.nvidia_api_key
        if not key.get_secret_value():
            print(json.dumps({"provider": provider, "status": "key_missing"}), flush=True)
            successes.append(False)
            continue
        selected = settings.model_copy(
            update={
                "llm_enabled": True,
                "llm_provider": provider,
                "llm_max_model_turns": 4,
                "llm_max_tool_calls": 2,
                "llm_timeout_seconds": 45,
                "llm_max_output_tokens": 1024,
                "llm_max_total_output_tokens": 4096,
                # Only the in-process helper is exercised; this does not configure a server.
                "app_api_token": SecretStr("in-process-smoke-only"),
            }
        )
        started = time.monotonic()
        print(json.dumps({"provider": provider, "status": "starting"}), flush=True)
        owned_model = None
        try:
            # The request borrows its diagnostic wrapper; this scope owns the
            # exact provider root, including when wrapper construction fails.
            owned_model = create_provider_model(selected)
            model = DiagnosticModel(owned_model)
            result = await answer_spending(
                tools,
                "Delegate to spending with task. The specialist must use summarize_spending "
                "for inclusive UTC dates 2026-11-01 to 2026-11-13. "
                "Report the observed net spending in USD and that history completeness is unknown. "
                "This is a synthetic integration test; no planning or other data tools are needed.",
                selected,
                model=model,
            )
            evidence_matches = any(
                row["tool"] == "summarize_spending"
                and row["result"].get("status") == "ok"
                and row["result"].get("net_spending_cents") == expected.net_spending_cents
                and row["result"].get("start") == "2026-11-01"
                and row["result"].get("end") == "2026-11-13"
                for row in result.evidence
            )
            passed = result.status == "answered" and evidence_matches
            successes.append(passed)
            print(
                json.dumps(
                    {
                        "provider": provider,
                        "model": result.model,
                        "status": result.status,
                        "passed": passed,
                        "evidence_matches": evidence_matches,
                        "model_calls": result.model_calls,
                        "tool_calls": result.tool_calls,
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "failure": model.failure,
                    }
                ),
                flush=True,
            )
        except Exception as exc:
            successes.append(False)
            print(
                json.dumps(
                    {
                        "provider": provider,
                        "status": "setup_failed",
                        "failure_type": type(exc).__name__,
                    }
                ),
                flush=True,
            )
        finally:
            if owned_model is not None:
                await close_owned_provider(owned_model)
    return 0 if all(successes) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Permit bounded paid provider calls")
    parser.add_argument("--provider", choices=["all", "gemini", "nemotron"], default="all")
    parser.add_argument("--settings", type=Path, default=Path("settings.ai.toml"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to authorize credential loading and provider calls")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
