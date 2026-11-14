# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/backend/pyproject.toml apps/backend/pyproject.toml

FROM python-build AS backend-tests
COPY apps/backend/src apps/backend/src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --all-packages
COPY tests/ tests/
COPY data/scenarios.json data/scenarios.json
COPY scripts/ scripts/
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["sh", "-c", "/app/.venv/bin/pytest -q -p no:cacheprovider tests/test_lab_*.py tests/test_capture_nessie_wallet.py"]

FROM python-build AS backend-build
COPY apps/backend/src apps/backend/src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable --package spending-api

FROM python:3.12-slim-bookworm AS backend
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app && mkdir /app/.data && chown app:app /app/.data
COPY --from=backend-build /app/.venv /app/.venv
COPY data/scenarios.json data/scenarios.json
COPY scripts/ scripts/
USER app
EXPOSE 8765
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=10 CMD ["python","-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2).close()"]
CMD ["uvicorn","spending_api.lab_api:create_app","--factory","--host","0.0.0.0","--port","8765"]
