"""Run the MVP's deterministic spending tools from a terminal, without Photon."""

import argparse
import asyncio
import json
from datetime import date

import httpx

from spending_api.agent_tools import SpendingTools
from spending_api.config import load_settings
from spending_api.models import DateRange
from spending_api.nessie import DataSourceError, load_snapshot


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["summary", "accounts", "recurring", "transactions"], default="summary",
        nargs="?",
    )
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--limit", type=int, default=20, help="Maximum transactions to print")
    args = parser.parse_args(argv)
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
            rows = tools.get_transactions(window)[:args.limit]
        result = {
            "source": snapshot.source,
            "items": [row.model_dump(mode="json") for row in rows],
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
