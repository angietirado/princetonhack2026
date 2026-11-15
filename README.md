# Campus Exchange

A student classifieds marketplace built with **React + TypeScript + Vite**, **FastAPI**, **MongoDB**, and **Docker Compose**. Enter a valid `.edu` address to use a lightweight browser profile. No passwords, emailed codes, or sign-in steps. Every profile can both buy and sell.

The check validates email format and the `.edu` domain only; it does not prove inbox ownership or enrollment. Buyer and seller arrange payment and pickup directly, like a classifieds marketplace. There is no checkout or payment processing.

See [docs/PLAN.md](docs/PLAN.md) for the endpoint contract and design.

## Start with Docker

Start Docker Desktop, then run:

```sh
docker compose up --build -d
```

Compose reads `.env`. On a fresh checkout, copy [.env.example](.env.example) to `.env` first, or run `docker compose --env-file .env.example up --build -d`. The example defaults to `DEVMODE=false`; this local workspace has `DEVMODE=true` for testing.

| Service | URL |
| --- | --- |
| Marketplace | http://localhost:8080 |
| API documentation | http://localhost:8001/docs |

Services are bound to localhost. MongoDB data survives restarts in the `campus-exchange_mongo_data` volume. No email service is required.

## Development profiles

Set `DEVMODE=true` in `.env`, then run `docker compose up -d --build`. A compact **Dev mode** bar lets you switch between **dev_student1** and **dev_student2** without entering an email. Both can buy, sell, and message each other. Each starts with an editable listing, so you can test both sides immediately.

These profiles keep the same identity, listings, and messages when you switch away and back or restart the app. Repeated startup does not reset edited or deleted listings. For two students at once, use separate browser/private windows.

Set `DEVMODE=false` and recreate the backend to disable the shortcuts. The server rejects development sessions/routes and hides development listings and conversations in that mode; it retains the data for your next development session. Ordinary `.edu` profiles continue to work normally. Direct Python development uses the same `DEVMODE` process environment variable.

## Try it

1. Choose **Check .edu email**, enter a student email and optional display name, then **Continue**.
2. Post an item, browse, and message sellers with that same profile.
3. Use another browser/private window for a second student during testing.
4. Agree on price, payment method, and pickup in the conversation. Complete payment outside the app.
5. Sellers can edit listings and mark them reserved or sold.

Your browser remembers the profile for seven days. Clearing the profile, losing its cookie, or using another browser starts a separate profile. Entering the same email does not recover another profile or grant access to its listings/messages.

Optional sample listings:

```sh
docker compose exec backend python -m app.seed
```

Samples are labeled **Demo** and cannot receive messages. This optional seeding is explicit and idempotent; a fresh database starts empty when development mode is disabled.

```sh
docker compose logs -f backend
docker compose stop
```

`stop` preserves the database.

## Local development

Prerequisites: Python 3.12+, Node.js 22.12+, and Docker for MongoDB.

```sh
docker compose up -d mongodb
python -m venv .venv
```

Activate `.venv` (`.venv\Scripts\Activate.ps1` on PowerShell or `source .venv/bin/activate` on macOS/Linux), then:

```sh
python -m pip install -r apps/backend/requirements-dev.txt
python -m uvicorn app.main:app --app-dir apps/backend --reload --port 8001
```

In another terminal:

```sh
cd apps/frontend
npm ci
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to FastAPI; Docker uses nginx for the same-origin proxy. Direct Python execution reads process environment variables; Compose reads `.env`. See [.env.example](.env.example).

## Checks

With the virtual environment active:

```sh
python -m pytest
npm --prefix apps/frontend run build
docker compose config --quiet
python scripts/smoke_test.py
```

Tests use isolated MongoDB test doubles by default. Set `TEST_MONGODB_URI=mongodb://localhost:27017` to use real MongoDB; tests create and remove only uniquely named test databases. The smoke test exercises the running Docker app with temporary browser profiles and cleans up its listings.

## Implementation notes

Profiles use opaque HttpOnly cookies and hashed server-side tokens. Listing edits require the owner profile; conversations require a participant. Public responses never expose email and explicitly report `email_verified: false`. A fresh browser always receives a new identity, even when the same email is entered. There are no buyer/seller account roles.

Compose is configured for local development. For deployment, use authenticated MongoDB, HTTPS with `COOKIE_SECURE=true`, exact `ALLOWED_ORIGINS`, and trusted proxy IPs for Uvicorn's forwarded headers. Domain-only eligibility is the intended prototype behavior until recoverable identity verification is added. Photos use optional HTTPS URLs rather than uploads.
