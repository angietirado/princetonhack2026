# Regulars

Customer intelligence for an independent business owner. Understand who returns, see which customer groups need attention, and turn observed purchase patterns into a measurable experiment.

This is the third independent approach, forked from Ledgerly commit `09a62d8`, on branch `feature/customer-intelligence`. The default experience uses a clearly labeled fictional cafe dataset. It does not treat one person's bank account as a business customer database.

## Run with Docker

```powershell
docker compose --env-file .env.example up --build --detach --wait
```

Open **http://localhost:5193**. API documentation: **http://localhost:8785/docs**.

The `regulars-lab` project uses its own ports and `owner-data` volume, so the original project and second approach can keep running. The private `.env` was copied unchanged and remains ignored by Git and Docker builds. Verification uses only `.env.example`; the merchant app never automatically loads dotenv files.

```powershell
docker compose --env-file .env.example --profile tools run --build --rm backend-tests
docker compose --env-file .env.example --profile tools run --build --rm dashboard-tests
docker compose --env-file .env.example exec -T backend python scripts/smoke_owner.py
```

Rebuild with the startup command after changes. Stop using `docker compose --env-file .env.example down`. Do not add `--volumes` when preserving stored data.

## Owner workflow

1. Inspect net sales, gross average purchase, active customers, and returning-customer share for an explicit UTC period. Within-period repeat buying is shown separately, so short imports do not confuse missing history with lost loyalty.
2. Explore observed customer segments and cohort retention. Open a customer to inspect the purchase/refund evidence behind their group; [customer evidence](docs/CUSTOMER_EVIDENCE.md) explains the period and history views.
3. Inspect suggested experiments and their supporting counts. Segment rules describe observed behavior; they are not churn predictions.
4. Ask a focused question and inspect the calculated evidence. The default investigator works without external credentials.

Amounts use integer cents. Refunds and incomplete observation windows remain explicit. Synthetic results are a product demonstration, not claims about a real business. No campaign or customer message is sent.

## Architecture and sponsorship

See [product decisions](docs/OWNER_DIRECTION.md) for what this fork retains, replaces, and plans next. Existing consumer-finance adapters remain available in source history and legacy modules but are outside the merchant entry point.

Optional sponsor integrations require a useful role and honest evidence of implementation. A real business consultation is needed before claiming Peri eligibility. Live provider calls and sponsor eligibility are not established by offline tests.

Sales-file format and import guarantees: [CSV contract](docs/CSV_IMPORT.md). Suggested walkthrough: [two-minute demo](docs/DEMO.md).


Real-browser checks run separately after the app is started:

```powershell
docker compose --env-file .env.example --profile tools run --build --no-deps --rm browser-tests
```

This checks desktop/mobile layout, filters, questions, exports and reviewed import persistence. It creates a clearly synthetic QA dataset and restores the previously selected dataset. Screenshots and the browser report are written to ignored `.data/browser-artifacts`.

Optional AI setup and verified sponsor context are documented in [sponsor fit](docs/SPONSOR_FIT.md). Keep `OWNER_AI_ENABLED=false` for credential-free checks. An operator who enables AI must provide `OWNER_API_TOKEN` and the selected provider key through their chosen runtime environment; this app does not read dotenv files itself.
The optional [Nessie merchant importer](docs/NESSIE_OWNER.md) saves explicitly synthetic sandbox datasets through a separate Docker tool. It is read-only against Nessie and does not silently change the current dataset.
