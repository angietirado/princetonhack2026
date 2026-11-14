import argparse
import json
import os
import random
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv


def build_fixture(seed: int, days: int, end: date, scenario: str = "baseline") -> dict:
    """Repeatable Nessie-shaped fixture. Same seed, end date, and options => same JSON."""
    rng = random.Random(seed)
    customer_id, account_id = "cus_demo", "acct_demo"
    merchants = [
        {"id": "mer_coffee", "name": "Campus Coffee", "category": "dining"},
        {"id": "mer_market", "name": "Neighborhood Market", "category": "groceries"},
        {"id": "mer_transit", "name": "City Transit", "category": "transport"},
        {"id": "mer_stream", "name": "Stream Club", "category": "subscriptions"},
    ]
    transactions = []

    def add(day: date, amount: int, merchant_id: str | None, kind="purchase", status="completed"):
        timestamp = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=12)
        tx = {
            "id": f"txn_demo_{len(transactions):04}",
            "payer_id": account_id,
            "kind": kind,
            "status": status,
            "medium": "balance",
            "amount_cents": amount,
            "created_at": timestamp.isoformat(),
            "updated_at": timestamp.isoformat(),
            "settle_at": timestamp.isoformat(),
            "description": f"Synthetic {kind}",
        }
        if merchant_id:
            tx["merchant_id"] = merchant_id
        transactions.append(tx)
        return tx

    start = end - timedelta(days=days - 1)
    for offset in range(days):
        day = start + timedelta(days=offset)
        multiplier = 3 if scenario == "overspending" and day > end - timedelta(days=14) else 1
        add(day, rng.randint(400, 1200) * multiplier, "mer_coffee")
        if offset % 7 == 0:
            add(day, rng.randint(3500, 8500), "mer_market")
        if day.weekday() < 5:
            add(day, 275, "mer_transit")
        if day.day == 1:
            add(day, 1299, "mer_stream")
        if day.day in {1, 15}:
            add(day, 150000, None, kind="deposit")
    original = add(end, 4500, "mer_market")
    refund = add(end, 1500, None, kind="refund")
    refund["parent_transaction_id"] = original["id"]
    add(end, 9000, "mer_market", status="pending")
    add(end, 5000, "mer_market", status="declined")
    transfer = add(end, 25000, None, kind="transfer")
    transfer["payee_id"] = "acct_other"
    return {
        "schema_version": 1,
        "seed": seed,
        "scenario": scenario,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "customer_id": customer_id,
        "accounts": [
            {
                "id": account_id,
                "customer_id": customer_id,
                "nickname": "Demo checking",
                "type": "Checking",
                "balance_cents": 225000,
            }
        ],
        "merchants": merchants,
        "transactions": transactions,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate local fixtures or Nessie v2 history")
    parser.add_argument("--target", choices=["local", "nessie"], default="local")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--end-date", type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument("--scenario", choices=["baseline", "overspending"], default="baseline")
    parser.add_argument("--transactions", type=int, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true", help="Actually create a Nessie simulation")
    args = parser.parse_args()
    if not 1 <= args.days <= 365 or not 1 <= args.transactions <= 5000:
        parser.error("days must be 1..365 and transactions must be 1..5000")
    if args.target == "local":
        payload = build_fixture(args.seed, args.days, args.end_date, args.scenario)
        output = args.output or Path(".data/demo.json")
    else:
        if args.scenario != "baseline":
            parser.error("--scenario overspending is local-only; Nessie uses its own scenarios")
        request = {
            "mode": "historical",
            "seed": args.seed,
            "customers": 1,
            "transactions": args.transactions,
            "duration_seconds": args.days * 86400,
            "anomaly_rate": 0.05,
        }
        if not args.apply:
            print(
                json.dumps(
                    {"dry_run": True, "method": "POST", "path": "/simulations", "body": request},
                    indent=2,
                )
            )
            return
        load_dotenv(dotenv_path=Path(".env"), encoding="utf-8")
        key = os.getenv("NESSIE_API_KEY")
        if not key:
            parser.error("Set NESSIE_API_KEY in .env before using --apply")
        settings_path = Path(os.getenv("APP_SETTINGS_FILE", "settings.toml"))
        try:
            with settings_path.open("rb") as source:
                options = tomllib.load(source)
            base = options.get("nessie_base_url", "https://v2-prod-api.nessieisreal.com")
        except (OSError, tomllib.TOMLDecodeError):
            parser.exit(1, "Read a valid settings.toml before creating Nessie data.\n")
        try:
            response = httpx.post(
                f"{base.rstrip('/')}/simulations",
                json=request,
                timeout=60,
                headers={"Authorization": f"Bearer {key}"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError:
            parser.exit(
                1,
                "Nessie simulation request failed. Check the dashboard before retrying; "
                "a timed-out request may have created data.\n",
            )
        output = args.output or Path(".data/nessie-run.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {args.target} data to {output}")


if __name__ == "__main__":
    main()
