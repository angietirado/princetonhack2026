"""Run deterministic spending tools or ask the running local AI backend, without Photon."""

import argparse
import asyncio
import json
from datetime import date

import httpx

from spending_api.agent_tools import SpendingTools
from spending_api.config import load_settings
from spending_api.llm import AgentAnswer
from spending_api.models import DateRange
from spending_api.nessie import DataSourceError, load_snapshot

ANSWER_URL = "http://127.0.0.1:8765/api/v1/agent/answer"
MAX_RESPONSE_BYTES = 512_000


def _ask(question: str, parser: argparse.ArgumentParser) -> None:
    """Send one request to the server; never call a model or load spending locally."""
    try:
        settings = load_settings()
        token = settings.app_api_token.get_secret_value()
        if not token:
            parser.exit(1, "APP_API_TOKEN is required to ask the backend.\n")
        with httpx.Client(
            timeout=httpx.Timeout(settings.llm_timeout_seconds + 5, connect=3),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "POST",
                ANSWER_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"question": question},
            ) as response:
                if response.status_code != 200:
                    message = {
                        401: "Backend rejected the app bearer token.",
                        403: "Backend denied this request.",
                        404: "The running backend does not expose the AI answer endpoint.",
                        429: "The backend is busy; retry shortly.",
                        503: "The AI backend is unavailable; check backend status and settings.",
                    }.get(response.status_code, "The backend could not complete this request.")
                    parser.exit(1, message + "\n")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        parser.exit(1, "The backend response exceeded the terminal size limit.\n")
                result = AgentAnswer.model_validate(json.loads(raw.decode("utf-8")))
    except httpx.HTTPError:
        parser.exit(
            1, "Unable to reach the local backend; check that it is running on port 8765.\n"
        )
    except (ValueError, OSError, TypeError, RecursionError):
        parser.exit(1, "Unable to read terminal settings or a valid backend answer.\n")
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    if result.status != "answered":
        parser.exit(1, "The backend did not produce a completed answer.\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["summary", "accounts", "recurring", "transactions", "ask"],
        default="summary",
        nargs="?",
    )
    parser.add_argument(
        "question", nargs="?", help="For ask: one quoted question (up to 2000 chars)"
    )
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--limit", type=int, help="Maximum transactions to print (default: 20)")
    args = parser.parse_args(argv)
    if args.command == "ask":
        if args.question is None or not args.question.strip() or len(args.question) > 2000:
            parser.error("ask requires one quoted question of 1 to 2000 characters")
        if args.start is not None or args.end is not None or args.limit is not None:
            parser.error("For ask, include dates and filters in the question")
        _ask(args.question.strip(), parser)
        return
    if args.question is not None:
        parser.error("A question is accepted only with ask")
    args.limit = 20 if args.limit is None else args.limit
    if args.start and args.end and args.start > args.end:
        parser.error("start must be on or before end")
    if not 1 <= args.limit <= 1000:
        parser.error("limit must be between 1 and 1000")
    try:
        settings = load_settings()
        snapshot = asyncio.run(load_snapshot(settings))
    except (ValueError, DataSourceError, httpx.HTTPError):
        parser.exit(1, "Unable to load spending; check settings.toml and provider credentials.\n")
    tools = SpendingTools(snapshot)
    window = DateRange(start=args.start, end=args.end)
    if args.command == "summary":
        result = tools.summarize_spending(window).model_dump(mode="json")
    else:
        if args.command == "accounts":
            rows = snapshot.accounts
        elif args.command == "recurring":
            rows = tools.find_recurring_charges(window)
        else:
            rows = tools.get_transactions(window)[: args.limit]
        result = {
            "source": snapshot.source,
            "items": [row.model_dump(mode="json") for row in rows],
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
