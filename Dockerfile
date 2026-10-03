# syntax=docker/dockerfile:1
# Multi-stage build following the official uv Docker guide
# (https://docs.astral.sh/uv/guides/integration/docker/, uv-docker-example
# multistage.Dockerfile): uv only in the builder, a plain Python runtime image.
#
# Migrations: the image does NOT migrate on startup. Run the same image once
# with `alembic upgrade head` before (re)starting the API: docker compose has a
# `migrate` service, deploy/deploy.sh runs a one-off container. This keeps
# start-up fast and avoids concurrent migrations from several containers.

FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
# Dependencies first (cached layer), then the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked


# Must match the builder's interpreter path (/usr/local/bin/python3.14).
FROM python:3.14-slim-trixie

RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

COPY --from=builder --chown=nonroot:nonroot /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER nonroot
WORKDIR /app
EXPOSE 8000

# Liveness only (no DB): /api/v1/health/live. Readiness with the DB is /api/v1/health.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2)"]

CMD ["uvicorn", "tuttitrip.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers"]
# X-Forwarded-* is honoured only from FORWARDED_ALLOW_IPS (uvicorn reads the
# variable; default 127.0.0.1). deploy.sh sets it to the gateway's network.
