# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /uvx /bin/

RUN groupadd --gid 10001 finintel \
    && useradd --uid 10001 --gid finintel --create-home --shell /bin/bash finintel

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --group ml --group platform --group rag --no-dev \
    && chown -R finintel:finintel /app

COPY --chown=finintel:finintel config ./config
COPY --chown=finintel:finintel src ./src
COPY --chown=finintel:finintel scripts ./scripts

RUN mkdir -p /app/dagster_home /app/data/metadata/dagster \
    && cp /app/config/dagster.postgres.yaml /app/dagster_home/dagster.yaml \
    && chown -R finintel:finintel /app/dagster_home /app/data

ENV PYTHONPATH=/app/src \
    DAGSTER_HOME=/app/dagster_home \
    PYTHONUNBUFFERED=1 \
    UV_NO_SYNC=1

USER finintel

CMD ["uv", "run", "--no-sync", "dagster-daemon", "run", "-m", "finintel.orchestration.definitions"]
