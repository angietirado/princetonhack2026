# Data generator

An independent Python package for generating the app's synthetic fixtures or starting a Nessie v2 historical simulation. It has no import dependency on the FastAPI app and is never invoked by a web request.

Run from the repository root:

```sh
uv run --package spending-data-generator generate-data --seed 42 --days 90 --end-date 2026-11-13
uv run --package spending-data-generator generate-data --scenario overspending
uv run --package spending-data-generator generate-data --target nessie
```

The same local seed, end date, day count, and scenario produce identical JSON. Local output defaults to `.data/demo.json`; choose another path with `--output`. The default end date is today's UTC date. Balances are illustrative; transactions exercise the spending-analysis contract rather than reconstructing a complete ledger.

The Nessie target prints a dry-run request unless `--apply` is supplied. With `--apply`, it reads `NESSIE_API_KEY` and `NESSIE_BASE_URL` from the environment or `.env`, creates a historical simulation, and writes the returned manifest to `.data/nessie-run.json`. It does not upload the local fixture. Nessie controls dates and anomaly scenarios for that target; `--scenario overspending` is local-only.

See [the integration guide](../docs/INTEGRATIONS.md) before applying a live generation request.
