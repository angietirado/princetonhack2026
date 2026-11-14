# Spending Companion

A hackathon app for analyzing Nessie v2 spending. The Ledgerly dashboard and terminal use the same pinned customer's backend data. Photon phone conversations are deferred. Docker Compose runs the dashboard, FastAPI backend, and separate data generator. A minimal React source scaffold remains in `apps/frontend`; the default dashboard is the root HTML/JavaScript application.

The project plan and decision log live in [docs/DESIGN.md](docs/DESIGN.md). The [integration guide](docs/INTEGRATIONS.md) covers credentials and provider setup; the [frontend handoff](apps/frontend/README.md) describes the API contract.

## Quick start with Docker

Docker is the default development workflow. Install and start Docker Desktop with Linux containers, or Docker Engine with Compose v2 or later. No host Python, uv, or Node.js installation is needed.

From the repository root, start all three components:

```sh
docker compose --env-file .env.example up --build --detach --wait
```

Open the app at **http://127.0.0.1:5173** and API documentation at **http://127.0.0.1:8765/docs**. Ports 5173 and 8765 must be free. The first build downloads the required images and dependencies.

This uses the credential-free `.env.example` and does not load your private `.env` file. The dashboard loads the generated fixture through FastAPI and labels it synthetic. For your pinned Nessie customer, start with `docker compose --env-file .env up --build --detach --wait` and enter your existing `APP_API_TOKEN` on the dashboard's unlock screen.

## How the three components run

| Component | Compose service | Startup behavior |
| --- | --- | --- |
| Data generator | `data-init` | Runs the generator package to create a missing demo fixture, then exits successfully. Existing data is preserved. |
| FastAPI backend | `backend` | Starts after generation finishes and reads the shared data. Stays running on port 8765. |
| Ledgerly dashboard | `frontend` | Starts after the backend is healthy. nginx serves the root `index.html`, `app.js`, and `styles.css` on port 5173, with proxy routes available for FastAPI. |

**The generator is a one-shot job.** Seeing `data-init` with `Exited (0)` is expected; the backend and frontend continue running. No additional startup command is needed for generation.

The `generator` service in the `tools` profile exposes the same package's CLI for intentional regeneration. It is separate from normal startup so restarting the app does not overwrite your data.

Fixtures and the Photon SQLite inbox live in the `app-data` named volume at `/app/.data`. The volume is shared by the Python containers and is separate from the host's `.data` folder. nginx proxies the dashboard's `/api/v1/dashboard` request to FastAPI on the same origin. The backend returns one customer-scoped snapshot for all visualizations.

## Everyday Docker commands

```sh
# See running services and the completed generator job.
docker compose --env-file .env.example ps --all

# Follow application logs.
docker compose --env-file .env.example logs -f backend frontend

# Rebuild and restart after changing source code.
docker compose --env-file .env.example up --build --detach --wait

# Stop and remove containers while keeping generated data.
docker compose --env-file .env.example down
```

Source code is built into the images, so Docker changes take effect after rebuilding. `docker compose down` preserves the data volume.

## Generate demo data

Run the generator inside Docker from the repository root. The first two commands intentionally replace the fixture in the shared volume:

```sh
# Fix the end date as well as the seed for identical fixtures on every machine.
docker compose --env-file .env.example --profile tools run --build --rm generator --seed 42 --days 90 --end-date 2026-11-13

# A synthetic scenario with higher dining spending in the last two weeks.
docker compose --env-file .env.example --profile tools run --build --rm generator --scenario overspending

# Show the Nessie request without making it.
docker compose --env-file .env.example --profile tools run --build --rm generator --target nessie
```

The `--build` flag keeps the tools-profile image up to date. In fixture mode, the backend reads the updated fixture on its next request; use Refresh in the dashboard to display it. Nessie mode reads the pinned provider customer instead. Live Nessie generation requires your credential file and an explicit `--apply`; see the [integration guide](docs/INTEGRATIONS.md) first.

## Provider configuration

Keep non-secret options in `settings.toml`, including `data_source`, `nessie_customer_id`, provider URLs, and `photon_enabled`. The MVP customer is pinned there; starting or restarting the app does not generate new Nessie data. Keep credentials such as `NESSIE_API_KEY` and `APP_API_TOKEN` in the ignored `.env`. Photon is disabled and needs no setup for the MVP.

For a fresh clone, copy `.env.example` only if `.env` does not exist, fill the credentials, and remove its `APP_SETTINGS_FILE` selector to use `settings.toml`. `docker compose --env-file .env.example ...` intentionally mounts `settings.example.toml` instead, so automated checks stay offline. Read the [integration guide](docs/INTEGRATIONS.md) for the complete setup.

To run with your configured provider settings, use `docker compose --env-file .env up --build --detach --wait`. Compose passes the relevant settings to the backend or generator at runtime. The frontend and offline initializer receive no provider keys. Re-run that command after configuration changes. `.env` is ignored by Git and excluded from Docker image builds; use the credential-free `.env.example` for shared configuration and validation.

The settings file is mounted read-only into the backend and generator. Keep `fixture_path` and `photon_inbox_path` under `.data/` so container data persists in the shared volume. Provider credentials stay out of the frontend and image build context.

## Terminal MVP

After starting the configured stack, run these from the repository root:

```sh
docker compose --env-file .env.example exec -T backend python -m spending_api.terminal summary
docker compose --env-file .env.example exec -T backend python -m spending_api.terminal recurring
docker compose --env-file .env.example exec -T backend python -m spending_api.terminal accounts
docker compose --env-file .env.example exec -T backend python -m spending_api.terminal transactions --limit 10
```

`exec` uses the running backend's mounted settings and runtime credentials; its `--env-file .env.example` does not switch that container to fixture mode. The commands use the pinned customer, return integer-cent amounts, and make no financial writes. Add `--start YYYY-MM-DD --end YYYY-MM-DD` to limit the transaction period. Natural-language questions use the separate opt-in API below.

## LangChain assistant

The backend supports Gemini and NVIDIA Nemotron through LangChain. It exposes only read-only spending tools, with server-bound customer data, bounded model/tool calls, and evidence alongside each answer. AI is disabled in the default offline settings.

In your ignored `.env`, set `GEMINI_API_KEY` or `NVIDIA_API_KEY`, retain or create `APP_API_TOKEN`, and set `APP_SETTINGS_FILE=./settings.ai.toml`. Select `llm_provider = "gemini"` or `"nemotron"` in `settings.ai.toml`. This file uses generated fixture data so the first AI run needs no Nessie credential. See [key setup and limitations](docs/INTEGRATIONS.md#ai-agents).

```sh
docker compose --env-file .env -p princeton-langchain-20261114 up --build --detach --wait
```

Open **http://127.0.0.1:8765/docs**, choose **Authorize**, enter `APP_API_TOKEN`, and try `POST /api/v1/agent/answer` with `{"question":"Summarize spending from 2026-11-01 through 2026-11-13."}`. `GET /api/v1/agent/status` reports the selected provider without exposing keys. Each question starts a new context; include dates and filters in follow-up requests. The dashboard does not yet display AI chat.

The answer's `status` must be `answered` before treating its prose as a completed response. `limited` or `unavailable` responses explain the failure and can include partial tool evidence. Evidence is calculated from observed records; model prose is an interpretation, and missing history is not proof of zero spending. Recurring results remain candidates.

## What runs today

- FastAPI with a unified dashboard snapshot, accounts, transactions, spending summaries, recurring-charge candidates, and OpenAPI documentation.
- Provider-neutral Python tools for agents, backed by deterministic calculations in integer cents.
- A deterministic data generator and an opt-in Nessie v2 historical simulation command.
- The connected Ledgerly dashboard: cash/card balances, expense-category and monthly charts, date filters, searchable transactions, recurring candidates, and CSV exports.
- Photon 2.0 signature verification, sender filtering, a durable SQLite inbox, and an explicit outbound transport function.

Opt-in LangChain AI orchestration is implemented for the question API. The inbox worker that sends phone replies is **not implemented yet**. Photon is disabled in the MVP settings; enabling its receiver later only queues events. The pinned Nessie customer comes from a completed 300-transaction historical simulation. Credentials are not committed.

## Checks with Docker

Run backend configuration/API tests and dashboard DOM regression tests inside Docker:

```sh
docker compose --env-file .env.example --profile tools run --build --rm backend-tests
docker compose --env-file .env.example --profile tools run --build --rm dashboard-tests
```

With the stack running, check the served dashboard and API routes from inside Docker:

```sh
docker compose --env-file .env.example exec -T backend python scripts/smoke_docker.py --frontend-url http://frontend:8080 --backend-url http://127.0.0.1:8765
```

GitHub Actions runs backend tests, dashboard regression tests, and Docker smoke checks on pushes and pull requests. Docker checks cover HTTP routing, the standalone generator, and data persistence across container recreation. Dependencies are locked in `uv.lock`, root `package-lock.json`, and `apps/frontend/package-lock.json`.

## Dashboard scope

The root `index.html`, `app.js`, and `styles.css` contain the connected Ledgerly dashboard served by the `frontend` container. The React source in `apps/frontend` is retained as a scaffold and is not the default Docker page.

The app token is kept only in page memory, sent in an Authorization header to the same-origin backend, and cleared on lock or reload. Provider keys never reach the browser. The customer stays fixed by `settings.toml`; there is no fake workspace switcher or bank-linking flow. Errors hide old figures instead of substituting sample data.

All amounts are calculated in cents. The category donut shows completed expenses before refunds; monthly bars and reports show net spending after refunds. Current cash balances exclude credit card debt. UTC date filters affect transactions, not current balances. CSV exports preserve the selected period; transaction exports include all matching rows across pages. Recurring charges are labeled candidates. The dashboard does not infer tax deductions or display AI answers.

Docker is sufficient for running and checking the dashboard. [Optional native development](docs/LOCAL_DEVELOPMENT.md) is available for working on the Python and React scaffolds without containers.

## Repository map

```text
apps/
  backend/src/spending_api/    FastAPI, read-only data adapter, agent tools, Photon boundary
  frontend/                   React source scaffold and frontend Dockerfile
index.html, app.js, styles.css Connected Ledgerly dashboard served by Docker
data-generator/               Independent fixture and Nessie simulation CLI
compose.yaml                  Startup for all three components and shared storage
settings.toml                 Readable runtime options and pinned Nessie customer
settings.example.toml         Offline options used by credential-free checks
Dockerfile                    Separate backend and generator image targets
docker/                       Fixture initialization and nginx routing
docs/                         Design, integration, and optional native setup
scripts/                      Docker smoke checks and optional Windows setup
tests/                        Dashboard interactions, money calculations, API, and webhooks
```
