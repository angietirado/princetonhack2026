# Optional native development

The default workflow runs all components with Docker; see the [root README](../README.md#quick-start-with-docker). Use this setup for host-based code reloads and unit tests. Stop the Docker stack with `docker compose down` before starting native servers on the same ports.

Native processes use the host's `.data` directory. They do not read the Docker `app-data` volume.

## Windows

From the repository root, with Node.js 22.12+ installed:

```powershell
.\scripts\bootstrap.ps1
```

Bootstrap installs uv and Python 3.12 into ignored directories in this checkout, installs locked dependencies, creates `.env` if missing, and generates the local fixture if missing.

Start the backend:

```powershell
.\scripts\uv.ps1 run --package spending-api uvicorn spending_api.main:create_app --factory --reload --host 127.0.0.1 --port 8765
```

Start the frontend in a second terminal:

```powershell
cd apps/frontend
npm run dev
```

The `scripts/uv.ps1` wrapper keeps Python downloads and caches inside this repository. On a machine with uv installed, use `uv` in place of `.\scripts\uv.ps1`.

## macOS and Linux

With [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 22.12+ installed, run from the repository root:

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

Open `http://127.0.0.1:5173`. API documentation is at `http://127.0.0.1:8765/docs`.

## Generate host fixtures

Run from the repository root; these commands write to the host `.data` folder:

```powershell
.\scripts\uv.ps1 run --package spending-data-generator generate-data --seed 42 --days 90 --end-date 2026-11-13
.\scripts\uv.ps1 run --package spending-data-generator generate-data --scenario overspending
```

On macOS or Linux, replace `.\scripts\uv.ps1` with `uv`. Fixture files and `.env` are ignored by Git.

## Run checks

```powershell
.\scripts\uv.ps1 run --all-packages pytest -q
cd apps/frontend
npm run build
```

Ruff is installed as a development tool for formatting and linting when needed. With a Docker fixture stack running, a host Python interpreter can also run `scripts/smoke_docker.py`; CI runs that check automatically.
