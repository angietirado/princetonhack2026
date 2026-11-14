"""Explicit read-only Nessie sandbox import; no .env loading or API writes."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date

from spending_api.merchant_import import DatasetStore
from spending_api.merchant_nessie import NessieImportError, import_from_nessie


def calendar_date(value):
    try:
        if len(value) != 10:
            raise ValueError
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError("Use a calendar date YYYY-MM-DD.") from None


def main(argv=None, *, fetch=import_from_nessie):
    parser = argparse.ArgumentParser(description="Preview selected-merchant synthetic Nessie records; API access is read-only.")
    parser.add_argument("--merchant-id", required=True, help="Explicit sandbox merchant ID; never inferred from an account.")
    parser.add_argument("--coverage-start", required=True, type=calendar_date)
    parser.add_argument("--coverage-end", required=True, type=calendar_date)
    parser.add_argument("--save", action="store_true", help="Save this fetched snapshot as a separate immutable dataset.")
    parser.add_argument("--select", action="store_true", help="Also select the saved dataset; requires --save.")
    parser.add_argument("--data-dir", default=os.environ.get("OWNER_DATA_DIR", ".data"))
    args = parser.parse_args(argv)
    if args.select and not args.save:
        parser.error("--select requires --save.")
    try:
        dataset, preview = asyncio.run(fetch(
            merchant_id=args.merchant_id, coverage_start=args.coverage_start,
            coverage_end=args.coverage_end, api_key=os.environ.get("NESSIE_API_KEY", ""),
        ))
        result = {**preview, "saved": False, "selected": False}
        if args.save:
            store = DatasetStore(args.data_dir)
            key = store.add(dataset, preview["preview_hash"], select=args.select)
            result.update(saved=True, selected=args.select, dataset_id=key)
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except NessieImportError as exc:
        print(json.dumps({"error": str(exc), "saved": False}), file=sys.stderr)
    except Exception:
        # Do not print provider payloads, keys, local paths, or SQL diagnostics.
        print(json.dumps({"error": "Import or local storage failed; no successful save was confirmed.", "saved": False}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
