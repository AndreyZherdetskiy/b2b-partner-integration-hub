FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin hubuser

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev --no-install-project

COPY app/ /app/app/
COPY celery_app/ /app/celery_app/
COPY partner_mock/ /app/partner_mock/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/alembic.ini

RUN uv sync --frozen --no-dev

USER hubuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/inbound/v1/health')" || exit 1

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
