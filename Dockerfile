# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
# All workspace manifests are needed to validate uv.lock, even for a single package.
COPY pyproject.toml uv.lock .python-version ./
COPY apps/backend/pyproject.toml apps/backend/pyproject.toml
COPY data-generator/pyproject.toml data-generator/pyproject.toml

FROM python-build AS backend-tests
COPY apps/backend/src apps/backend/src
COPY data-generator/src data-generator/src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --all-packages
COPY tests/ tests/
COPY settings.example.toml settings.toml
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["/app/.venv/bin/pytest", "-q", "-p", "no:cacheprovider"]

FROM python-build AS backend-build
COPY apps/backend/src apps/backend/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package spending-api

FROM python-build AS generator-build
COPY data-generator/src data-generator/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package spending-data-generator

FROM python:3.12-slim-bookworm AS python-runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /app/.data && chown app:app /app/.data
USER app

FROM python-runtime AS data-generator
COPY --from=generator-build /app/.venv /app/.venv
COPY docker/ensure_fixture.py /app/docker/ensure_fixture.py
ENTRYPOINT ["generate-data"]

FROM python-runtime AS backend
COPY --from=backend-build /app/.venv /app/.venv
COPY scripts/smoke_docker.py /app/scripts/smoke_docker.py
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2).close()"]
CMD ["uvicorn", "spending_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765"]
