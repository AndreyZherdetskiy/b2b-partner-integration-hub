from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.middleware.correlation import CorrelationMiddleware
from app.api.middleware.max_body import MaxBodySizeMiddleware
from app.api.v1.admin import analytics as admin_analytics
from app.api.v1.admin import dead_letters as admin_dead_letters
from app.api.v1.admin import deliveries as admin_deliveries
from app.api.v1.admin import endpoints as admin_endpoints
from app.api.v1.admin import partners as admin_partners
from app.api.v1.admin import replay_approvals as admin_replay_approvals
from app.api.v1.admin import schemas as admin_schemas
from app.api.v1.inbound import events as inbound_events
from app.api.v1.internal import outbound as internal_outbound
from app.api.v1.partner import deliveries as partner_deliveries
from app.config import get_settings
from app.integrations.redis_client import close_redis_pool, create_redis_client, create_redis_pool
from app.logging import configure_logging
from app.observability.otel import configure_otel, instrument_fastapi, shutdown_otel

OPENAPI_TAGS = [
    {
        "name": "inbound",
        "description": (
            "Partner-facing webhook ingest. HMAC-SHA256 over timestamp and raw body; "
            "API key in Authorization."
        ),
    },
    {
        "name": "admin",
        "description": (
            "Operator API. Identifiers are UUIDv7 public ids only. "
            "Replay requires reason and is audited."
        ),
    },
    {
        "name": "internal",
        "description": (
            "Platform services only. Not partner-facing. " "partner_id is the partner public UUID."
        ),
    },
    {
        "name": "partner",
        "description": "Partner read-only APIs for own deliveries only; not admin.",
    },
    {"name": "health", "description": "Liveness and readiness probes."},
]

CORRELATION_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    422: {
        "description": "Invalid X-Correlation-Id (must be UUID version 7).",
    },
}


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(
        ...,
        description="Liveness status; ok when the process accepts traffic.",
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    configure_otel(settings.otel_service_name, settings)
    engine = create_async_engine(settings.database_url, pool_pre_ping=False)
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    redis_pool = create_redis_pool(settings)
    app.state.redis_pool = redis_pool
    app.state.redis = create_redis_client(redis_pool)
    # Persist path is outbox-only; API does not publish to Kafka.
    app.state.kafka_producer = None
    yield
    await close_redis_pool(redis_pool)
    await engine.dispose()
    shutdown_otel()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        lifespan=_lifespan,
        title="Partner Integration Hub",
        summary="At-least-once B2B webhook delivery with HMAC, retries, DLQ, and audited replay.",
        description=(
            "Inbound uses HMAC-SHA256 (`X-Hub-Signature-256`) over `{timestamp}.{raw_body}` "
            "and Bearer API keys. First accept returns **202**; duplicate Idempotency-Key "
            "returns **200**. JSON `id` fields are UUIDv7 public identifiers, never sequential "
            "database keys. Correlation `X-Correlation-Id` is UUIDv7. Internal outbound is "
            "tagged `internal` and is not partner-facing."
        ),
        openapi_tags=OPENAPI_TAGS,
        servers=[{"url": "http://localhost:8000", "description": "Local Compose"}],
        contact=None,
        license_info=None,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        MaxBodySizeMiddleware,
        max_body_size=settings.hub_max_payload_bytes,
    )
    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(
        "/inbound/v1/health",
        tags=["health"],
        summary="Inbound liveness probe",
        description="Process liveness for the inbound listener. No authentication.",
        response_model=HealthResponse,
        responses=CORRELATION_ERROR_RESPONSES,
    )
    async def inbound_health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/internal/v1/health",
        tags=["health"],
        summary="Internal liveness probe",
        description="Process liveness for internal platform callers. No authentication.",
        response_model=HealthResponse,
        responses=CORRELATION_ERROR_RESPONSES,
    )
    def internal_health() -> HealthResponse:
        return HealthResponse(status="ok")

    app.include_router(admin_partners.router)
    app.include_router(admin_analytics.router)
    app.include_router(admin_deliveries.router)
    app.include_router(admin_replay_approvals.router)
    app.include_router(admin_dead_letters.router)
    app.include_router(admin_endpoints.router)
    app.include_router(admin_schemas.router)
    app.include_router(inbound_events.router)
    app.include_router(internal_outbound.router)
    app.include_router(partner_deliveries.router)

    register_exception_handlers(app)
    instrument_fastapi(app, settings)

    return app
