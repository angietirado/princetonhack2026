# Blockwise
A Houston shopping experiment with two apps: a participant challenge and an analytics workspace.
This fork starts from Ledgerly commit 09a62d8 and develops on feature/houston-shopping-lab.

## Run locally with Docker
```powershell
docker compose --env-file .env.example up --build --detach --wait
```
- Participant app: http://localhost:5183
- Analytics app: http://localhost:5184
- API health: http://localhost:8775/health

No external credentials are needed. The project is named blockwise-lab and uses distinct ports and its own lab-data volume.
The original repository and its running containers are independent.
Stop with `docker compose --env-file .env.example down`; do not add `--volumes` unless intentionally deleting collected sessions.

## What is real, and what is simulated?
Selected home coordinates come from public-display records in the supplied historical Houston CoStar kit.
Stores, store positions, prices, budgets, and travel assumptions are simulated. Housing values do not determine participant income.
Participants make real choices inside this experiment; their choices are not actual bank transactions or representative Houston consumer data.
The fixed basket makes plan comparisons meaningful. Total time includes travel, shopping, parking when driving, and return home.
Analytics separates demonstration sessions from participant submissions and only computes paired savings when constraints match.

## Verify
```powershell
docker compose --env-file .env.example --profile tools run --build --rm backend-tests
docker compose --env-file .env.example --profile tools run --build --rm dashboard-tests
```
The private .env was copied directly from the original project as requested; it is ignored by Git and excluded from Docker build contexts.
No verification command should load it. Photon is being considered as an optional messaging or voice interface; it is not implemented here yet.

## Development
See [implementation contract](docs/LAB_CONTRACT.md) and [progress](docs/PROGRESS.md).
The small versioned seed at data/scenarios.json is included; the full CoStar kit stays ignored.
To rebuild the seed from the local kit, run scripts/build_scenarios.py with Python 3.12 or inside a temporary Python container.

## Demonstration records and browser checks
Generate explicit demo sessions without creating participant observations:
```powershell
docker compose --env-file .env.example exec -T backend python scripts/seed_demo.py
```
Open http://localhost:5183/?demo=1 for a demonstration challenge. The analytics app defaults to genuine participant submissions; select Demo data to inspect generated examples.

```powershell
docker compose --env-file .env.example exec -T backend python scripts/smoke_lab.py
docker compose --env-file .env.example --profile tools run --build --no-deps --rm browser-tests
```
Browser screenshots and reports are written to ignored .data/browser-artifacts. Start the three app services before running browser tests.

The guide's quick commands work offline. Optional model interpretation uses LAB_AI_ENABLED=true, LAB_AI_PROVIDER=gemini or nvidia, and an optional LAB_AI_MODEL with the corresponding provider key. These variables must be set by the operator; routine verification uses the credential-free template. Model proposals never automatically save a shopping decision.

See [product direction](docs/PRODUCT_DIRECTION.md) for the current integration ideas and packet findings. Sponsor/track selection remains open.