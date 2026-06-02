from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://hub:hub@postgres:5432/hub",
        description="Primary PostgreSQL DSN (async SQLAlchemy).",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        description="Redis URL for idempotency cache and rate limits.",
    )
    celery_broker_url: str = Field(
        default="redis://redis:6379/1",
        description="Celery broker URL (Redis DB 1 — separate from cache DB 0).",
    )
    kafka_bootstrap_servers: str = Field(
        default="kafka:9092",
        description="Kafka bootstrap servers for the event bus.",
    )
    fernet_key: str = Field(
        default="",
        description="Fernet key for encrypting signing secrets at rest.",
    )
    admin_bootstrap_token: str = Field(
        default="",
        description="Stage 1 static admin bootstrap token (demo only).",
    )
    log_level: str = Field(default="INFO", description="Root log level.")
    app_version: str = Field(
        default="0.1.0",
        description="Application version for OTel service.version.",
    )
    deployment_environment: str = Field(
        default="local",
        description="Deployment environment for OTel deployment.environment.",
    )

    otel_exporter_otlp_endpoint: str = Field(
        default="http://otel-collector:4318",
        description="OTLP HTTP exporter base URL (Collector).",
    )
    otel_service_name: str = Field(
        default="hub-api",
        description="OpenTelemetry service.name resource attribute.",
    )
    otel_sdk_disabled: bool = Field(
        default=False,
        description="Disable OpenTelemetry SDK when true.",
    )

    hub_max_attempts_default: int = 8
    hub_backoff_base_seconds: int = 30
    hub_backoff_multiplier: int = 2
    hub_backoff_max_seconds: int = 3600
    hub_inbound_timestamp_tolerance: int = 300
    hub_http_connect_timeout_ms: int = 3000
    hub_http_read_timeout_ms: int = 10000
    hub_idempotency_ttl_hours: int = 24
    hub_circuit_failure_threshold: int = 10
    hub_circuit_window_seconds: int = 60
    hub_circuit_open_seconds: int = 300
    hub_rate_limit_rps_default: int = 100
    hub_secret_rotation_overlap_hours: int = 24
    hub_sla_seconds_default: int = 60
    hub_dlq_age_alert_seconds: int = 3600
    hub_max_payload_bytes: int = Field(
        default=256 * 1024,
        description="Maximum request payload size in bytes (Stage 1: 256 KB).",
    )
    hub_replay_approval_required: bool = Field(
        default=False,
        description="When true, delivery replay requires admin approval before re-queueing.",
    )

    # Compose/.env often has OTEL_SDK_DISABLED= (empty) — pydantic bool rejects "".
    @field_validator("otel_sdk_disabled", "hub_replay_approval_required", mode="before")
    @classmethod
    def empty_bool_is_false(cls, value: object) -> object:
        if value == "":
            return False
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
