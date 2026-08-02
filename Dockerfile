# syntax=docker/dockerfile:1
# Multi-stage build with two runnable targets:
#   api — FastAPI screening service (embeddings on by default)
#   ui  — Streamlit tester
# Build the lite API without the ML stack: --build-arg WITH_EMBEDDINGS=0

ARG WITH_EMBEDDINGS=1

FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app


FROM base AS api-build
ARG WITH_EMBEDDINGS
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$WITH_EMBEDDINGS" = "1" ]; then \
        uv sync --frozen --no-dev --no-install-project --no-editable --extra embeddings; \
    else \
        uv sync --frozen --no-dev --no-install-project --no-editable; \
    fi
COPY src ./src
COPY config ./config
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$WITH_EMBEDDINGS" = "1" ]; then \
        uv sync --frozen --no-dev --no-editable --extra embeddings; \
    else \
        uv sync --frozen --no-dev --no-editable; \
    fi
# Bake the HF model and the precomputed vectors into the image so containers
# start instantly and run fully offline.
ENV HF_HOME=/opt/hf-cache
COPY data/sanctions.db ./data/sanctions.db
RUN if [ "$WITH_EMBEDDINGS" = "1" ]; then \
        .venv/bin/python -c "\
from sanctionscreen.config import Settings; \
from sanctionscreen.db import connect; \
from sanctionscreen.matching.embedding import precompute_embeddings; \
s = Settings(); conn = connect(s.database.path); \
n = precompute_embeddings(conn, s.embedding.model, s.embedding.vectors_path); \
print(f'{n} vectors precomputed')"; \
    else mkdir -p /opt/hf-cache; fi


FROM python:3.12-slim AS api
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/opt/hf-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PYTHONUNBUFFERED=1
COPY --from=api-build /app/.venv ./.venv
COPY --from=api-build /opt/hf-cache /opt/hf-cache
COPY --from=api-build /app/data ./data
COPY config ./config
COPY data/cache ./data/cache
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=4).raise_for_status()"
CMD ["uvicorn", "sanctionscreen.api.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS ui-build
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-editable --extra ui


FROM python:3.12-slim AS ui
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=ui-build /app/.venv ./.venv
COPY ui ./ui
EXPOSE 8501
CMD ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
