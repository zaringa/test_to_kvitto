FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.12-slim AS base
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./

FROM base AS test
RUN uv sync --frozen
COPY app ./app
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
RUN uv sync --frozen --no-dev
COPY app ./app
RUN useradd --uid 10001 --create-home appuser
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

