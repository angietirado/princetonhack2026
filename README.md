# Spending Companion

A hackathon app for analyzing Nessie v2 spending through AI agents and phone conversations on Photon 2.0. FastAPI owns the backend; React is a deliberately small starting point for the frontend teammate. A separate Python package creates demo data.

The project plan and decision log live in [docs/DESIGN.md](docs/DESIGN.md). The [integration guide](docs/INTEGRATIONS.md) covers credentials and provider setup; the [frontend handoff](apps/frontend/README.md) describes the API contract.

## What runs today

- FastAPI with accounts, transactions, spending summaries, recurring-charge candidates, and OpenAPI documentation.
- Provider-neutral Python tools for agents, backed by deterministic calculations in integer cents.
- A deterministic local data generator and an opt-in Nessie v2 historical simulation command.
- A React + TypeScript + Vite page that displays data from the backend.
- Photon 2.0 signature verification, sender filtering, a durable SQLite inbox, and an explicit outbound transport function.

AI orchestration and the inbox worker that sends replies are **not implemented yet**. The webhook queues events; it does not answer messages. External credentials are not included, and live Nessie/Photon calls have not been verified with an account.

## Quick start on Windows

Run from the repository root. Node.js 22.12+ is required. Bootstrap installs uv and Python 3.12 into ignored folders inside this checkout, installs locked dependencies, creates `.env` if missing, and generates the local fixture if missing.

```powershell
.\scripts\bootstrap.ps1
```

Start the backend:

```powershell
.\scripts\uv.ps1 run --package spending-api uvicorn spending_api.main:create_app --factory --reload --host 127.0.0.1 --port 8765
```

Start the frontend in a second terminal:

```powershell
cd apps/frontend
npm run dev
```

Open `http://127.0.0.1:5173`. Interactive API documentation is at `http://127.0.0.1:8765/docs`.

The `scripts/uv.ps1` wrapper keeps Python downloads and caches inside this repository. On a machine with uv installed, use `uv` in place of `.\scripts\uv.ps1` in every command below.

## Quick start on macOS or Linux

With [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 22.12+ installed:

```sh
uv sync --all-packages --locked
cp -n .env.example .env
uv run --package spending-data-generator generate-data
cd apps/frontend
npm ci
npm run dev
```

Start the backend from the repository root in another terminal:

```sh
uv run --package spending-api uvicorn spending_api.main:create_app --factory --reload --host 127.0.0.1 --port 8765
```

## Generate demo data

```powershell
# Fix the end date as well as the seed for identical fixtures on every machine.
.\scripts\uv.ps1 run --package spending-data-generator generate-data --seed 42 --days 90 --end-date 2026-11-13

# A local scenario with higher dining spending in the last two weeks.
.\scripts\uv.ps1 run --package spending-data-generator generate-data --scenario overspending

# Show the Nessie request without making it.
.\scripts\uv.ps1 run --package spending-data-generator generate-data --target nessie
```

The generator defaults to `.data/demo.json`. Local fixtures and `.env` are ignored by Git. Live generation requires an explicit `--apply`; see the integration guide before running it.

## Check the project

```powershell
.\scripts\uv.ps1 run --all-packages pytest -q
cd apps/frontend
npm run build
```

GitHub Actions runs these checks for pushes and pull requests. Python dependencies are locked in `uv.lock`; frontend dependencies are locked in `apps/frontend/package-lock.json`. Ruff is available as a development tool for formatting and linting when needed.

## Repository map

```text
apps/
  backend/src/spending_api/    FastAPI, read-only data adapter, agent tools, Photon boundary
  frontend/                   Minimal React app; owned by the frontend teammate
data-generator/               Independent fixture and Nessie simulation CLI
docs/                         Design, decisions, and integration instructions
scripts/                      Repo-local Windows setup
tests/                        Money calculations, scope, API, and webhook behavior
```
