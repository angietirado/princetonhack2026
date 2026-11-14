# Data generator

An independent Python package for generating the app's synthetic fixtures or starting a Nessie v2 historical simulation. It has no import dependency on the FastAPI app and is never invoked by a web request.

## Run with Docker

From the repository root:

```sh
docker compose up --build --detach --wait
```

Normal stack startup runs this package through the one-shot `data-init` service. It creates the initial fixture only when one is missing, then exits before the backend and frontend start. The generator remains a separate package and container image; it is not part of the web request process.

To intentionally replace the fixture or preview a Nessie generation request:

```sh
docker compose --profile tools run --build --rm generator --seed 42 --days 90 --end-date 2026-11-13
docker compose --profile tools run --build --rm generator --scenario overspending
docker compose --profile tools run --build --rm generator --target nessie
```

The `--build` flag rebuilds the standalone tools-profile image when its source has changed; the normal stack startup does not rebuild this profile-only service. The container works in `/app`, so the default `.data/demo.json` path resolves into the shared `app-data` volume. This is separate from the host's `.data` directory. The backend sees the updated fixture on its next request. Files placed outside `.data` are not retained after the temporary generator container exits.

The same local seed, end date, day count, and scenario produce identical JSON. Local output defaults to `.data/demo.json`; choose another path with `--output`. The default end date is today's UTC date. Balances are illustrative; transactions exercise the spending-analysis contract rather than reconstructing a complete ledger.

## Nessie generation

The Nessie target prints a dry-run request unless `--apply` is supplied. With `--apply`, it reads `NESSIE_API_KEY` from the environment or `.env`, reads `nessie_base_url` from `settings.toml`, creates a historical simulation, and writes the returned manifest to `.data/nessie-run.json`. `APP_SETTINGS_FILE` can select another options file. It does not upload the local fixture. Nessie controls dates and anomaly scenarios for that target; `--scenario overspending` is local-only. The MVP already has a pinned customer; do not regenerate it during normal startup.

See [the integration guide](../docs/INTEGRATIONS.md) before applying a live generation request.

## Optional local development without Docker

With the Python environment installed using the [optional native setup](../docs/LOCAL_DEVELOPMENT.md), run from the repository root:

```sh
uv run --package spending-data-generator generate-data --seed 42 --days 90 --end-date 2026-11-13
uv run --package spending-data-generator generate-data --scenario overspending
uv run --package spending-data-generator generate-data --target nessie
```

On Windows, use `.\scripts\uv.ps1` instead of `uv` if you installed the repo-local environment. These commands write to the host's `.data` folder rather than the Docker volume.
