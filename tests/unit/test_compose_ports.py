"""Compose data-plane invariants (frozen ports, KRaft, migrate-before-app)."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.fernet import Fernet

COMPOSE_PATH = Path("docker-compose.yml")
MAKEFILE_PATH = Path("Makefile")
TOPICS_SCRIPT = Path("infra/kafka/create-topics.sh")
KAFKA_INIT_DOCKERFILE = Path("infra/kafka/Dockerfile")
GRAFANA_DOCKERFILE = Path("infra/grafana/Dockerfile")
GRAFANA_DASHBOARDS_YML = Path("infra/grafana/provisioning/dashboards/dashboards.yml")

GRAFANA_DASHBOARDS_PATH = "/etc/grafana/dashboards"
FORBIDDEN_GRAFANA_DASHBOARDS_PATH = "/var/lib/grafana/dashboards"

NO_ENV_FILE_SERVICES = (
    "postgres",
    "hub-migrate",
    "hub-api",
    "hub-outbound-worker",
    "hub-outbox-relay",
    "hub-celery-worker",
    "hub-celery-beat",
)

FROZEN_HOST_PORTS = (
    ("postgres", "5432"),
    ("redis", "6379"),
    ("kafka", "9092"),
    ("hub-api", "8000"),
    ("hub-admin-ui", "8080"),
    ("otel-collector", "4317"),
    ("otel-collector", "4318"),
    ("prometheus", "9090"),
    ("grafana", "3000"),
    ("jaeger", "16686"),
    ("partner-mock", "8090"),
    ("kafbat-ui", "8081"),
    ("redis-commander", "8082"),
    ("adminer", "8083"),
    ("flower", "8084"),
)

REQUIRED_TOPICS = (
    "hub.outbound.pending",
    "hub.outbound.retry.30s",
    "hub.outbound.retry.1m",
    "hub.outbound.dlq",
    "hub.inbound.order.created",
    "hub.inbound.order.updated",
    "hub.integration.sla_breached",
)

MIGRATE_DEPENDENTS = (
    "hub-api",
    "hub-outbound-worker",
    "hub-outbox-relay",
    "hub-celery-worker",
    "hub-celery-beat",
)

HUB_PROCESS_HEALTHCHECK_SERVICES = (
    "hub-outbound-worker",
    "hub-outbox-relay",
    "hub-celery-worker",
    "hub-celery-beat",
)

BAKED_CONFIG_SERVICES = (
    "kafka-init",
    "otel-collector",
    "prometheus",
    "grafana",
)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def services(compose: dict[str, Any]) -> dict[str, Any]:
    return compose["services"]


def _port_strings(service: dict[str, Any]) -> list[str]:
    ports = service.get("ports") or []
    return [p if isinstance(p, str) else str(p) for p in ports]


def _bind_mount_sources(service: dict[str, Any]) -> list[str]:
    volumes = service.get("volumes") or []
    sources: list[str] = []
    for volume in volumes:
        if isinstance(volume, str) and ":" in volume:
            sources.append(volume.split(":", 1)[0])
    return sources


def _healthcheck_test(service: dict[str, Any]) -> str:
    healthcheck = service.get("healthcheck") or {}
    return " ".join(str(part) for part in (healthcheck.get("test") or []))


@pytest.mark.parametrize(("service_name", "host_port"), FROZEN_HOST_PORTS)
def test_frozen_host_port(services: dict[str, Any], service_name: str, host_port: str) -> None:
    flat = _port_strings(services[service_name])
    assert any(host_port in mapping for mapping in flat)


def test_kafka_is_kraft_without_zookeeper(services: dict[str, Any]) -> None:
    for name in services:
        assert "zookeeper" not in name.lower()
    kafka_env = services["kafka"].get("environment") or {}
    env_text = " ".join(f"{key}={value}" for key, value in kafka_env.items()).lower()
    assert "zookeeper" not in env_text
    assert "kraft" in env_text or "process_roles" in env_text


@pytest.mark.parametrize("name", ("postgres", "redis", "kafka"))
def test_data_plane_health_restart_logging(services: dict[str, Any], name: str) -> None:
    svc = services[name]
    assert "healthcheck" in svc
    assert svc.get("restart") == "unless-stopped"
    logging = svc.get("logging") or {}
    assert logging.get("driver") == "json-file"
    assert logging.get("options", {}).get("max-size") == "10m"


def test_kafka_init_waits_for_healthy_broker(services: dict[str, Any]) -> None:
    depends = services["kafka-init"].get("depends_on") or {}
    kafka_dep = depends.get("kafka") if isinstance(depends, dict) else None
    assert kafka_dep is not None
    assert kafka_dep.get("condition") == "service_healthy"


def test_create_topics_script_exists_on_disk() -> None:
    assert TOPICS_SCRIPT.is_file()


def test_kafka_init_dockerfile_sets_script_mode_without_root_chmod() -> None:
    """apache/kafka:4.3.1 runs as appuser; RUN chmod after COPY is EPERM."""
    text = KAFKA_INIT_DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM apache/kafka:4.3.1" in text
    assert "COPY --chmod=0755 create-topics.sh /create-topics.sh" in text
    assert "RUN chmod" not in text


@pytest.mark.parametrize(
    ("service_name", "image"),
    (
        ("postgres", "postgres:16.15"),
        ("redis", "redis:8.10"),
        ("kafka", "apache/kafka:4.3.1"),
    ),
)
def test_data_plane_image_tags(services: dict[str, Any], service_name: str, image: str) -> None:
    assert services[service_name].get("image") == image


@pytest.mark.parametrize("name", BAKED_CONFIG_SERVICES)
def test_baked_config_services_have_no_host_bind_mounts(
    services: dict[str, Any], name: str
) -> None:
    for source in _bind_mount_sources(services[name]):
        assert not source.startswith("./infra")
        assert not source.startswith("./docs/grafana")


@pytest.mark.parametrize(
    ("name", "context", "dockerfile"),
    (
        ("kafka-init", "./infra/kafka", "Dockerfile"),
        ("otel-collector", "./infra/otel", "Dockerfile"),
        ("prometheus", "./infra/prometheus", "Dockerfile"),
        ("grafana", ".", "infra/grafana/Dockerfile"),
    ),
)
def test_observability_services_bake_configs_in_images(
    services: dict[str, Any], name: str, context: str, dockerfile: str
) -> None:
    build = services[name].get("build") or {}
    assert build.get("context") == context
    assert build.get("dockerfile") == dockerfile


@pytest.mark.parametrize("topic", REQUIRED_TOPICS)
def test_create_topics_script_lists_topic(topic: str) -> None:
    script = TOPICS_SCRIPT.read_text(encoding="utf-8")
    assert topic in script


def test_no_tempo_service(services: dict[str, Any]) -> None:
    for name in services:
        assert "tempo" not in name.lower()


def test_compose_project_network_and_volume(compose: dict[str, Any]) -> None:
    assert compose.get("name") == "b2b-partner-integration-hub"
    default = (compose.get("networks") or {}).get("default") or {}
    assert default.get("name") == "b2b-partner-integration-hub"
    postgres_vol = (compose.get("volumes") or {}).get("postgres-data") or {}
    assert postgres_vol.get("name") == "b2b-partner-integration-hub-postgres-data"


def test_postgres_and_grafana_credentials_from_env(services: dict[str, Any]) -> None:
    postgres_env = services["postgres"].get("environment") or {}
    grafana_env = services["grafana"].get("environment") or {}
    assert postgres_env.get("POSTGRES_PASSWORD") == "${POSTGRES_PASSWORD}"
    assert grafana_env.get("GF_SECURITY_ADMIN_USER") == "${GF_SECURITY_ADMIN_USER}"
    assert grafana_env.get("GF_SECURITY_ADMIN_PASSWORD") == "${GF_SECURITY_ADMIN_PASSWORD}"


@pytest.mark.parametrize("name", NO_ENV_FILE_SERVICES)
def test_no_env_file_on_app_and_postgres_services(services: dict[str, Any], name: str) -> None:
    """Host .env has localhost DSNs — must not dump into containers (C3)."""
    assert "env_file" not in services[name]


def test_grafana_dashboards_provisioned_outside_data_volume() -> None:
    """Dashboards must live under /etc/grafana so grafana_data cannot shadow them (C2)."""
    dockerfile = GRAFANA_DOCKERFILE.read_text(encoding="utf-8")
    assert GRAFANA_DASHBOARDS_PATH in dockerfile
    assert FORBIDDEN_GRAFANA_DASHBOARDS_PATH not in dockerfile

    doc = yaml.safe_load(GRAFANA_DASHBOARDS_YML.read_text(encoding="utf-8"))
    providers = doc.get("providers") or []
    assert providers, "dashboards.yml: expected providers"
    path = providers[0].get("options", {}).get("path")
    assert path == GRAFANA_DASHBOARDS_PATH


def test_grafana_data_volume_mounts_var_lib_only(
    services: dict[str, Any], compose: dict[str, Any]
) -> None:
    volumes = services["grafana"].get("volumes") or []
    assert "grafana-data:/var/lib/grafana" in volumes
    grafana_vol = (compose.get("volumes") or {}).get("grafana-data") or {}
    assert grafana_vol.get("name") == "b2b-partner-integration-hub-grafana-data"


def test_compose_up_uses_wait() -> None:
    text = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "compose-up:" in text
    # Target body must wait for healthchecks (C5).
    after = text.split("compose-up:", 1)[1]
    target_body = after.split("\n\n", 1)[0]
    assert "--wait" in target_body


def test_runtime_dsn_interpolates_postgres_env() -> None:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres" in compose_text
    assert "postgresql+asyncpg://hub:hub@" not in compose_text


def test_env_example_lists_required_secrets() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")
    required = (
        "POSTGRES_USER=",
        "POSTGRES_PASSWORD=",
        "POSTGRES_DB=",
        "GF_SECURITY_ADMIN_USER=",
        "GF_SECURITY_ADMIN_PASSWORD=",
        "FERNET_KEY=",
        "ADMIN_BOOTSTRAP_TOKEN=",
        "CELERY_BROKER_URL=redis://localhost:6379/1",
    )
    missing = [key for key in required if key not in text]
    assert missing == []


def test_env_example_fernet_key_is_urlsafe_base64_32_bytes() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if row.startswith("FERNET_KEY="))
    Fernet(line.split("=", 1)[1].encode("ascii"))


def test_kafbat_uses_in_compose_listener(services: dict[str, Any]) -> None:
    env = services["kafbat-ui"].get("environment") or {}
    env_text = " ".join(f"{key}={value}" for key, value in env.items())
    assert "kafka:19092" in env_text


def test_hub_migrate_completes_before_app_services(services: dict[str, Any]) -> None:
    migrate = services["hub-migrate"]
    command = migrate.get("command") or []
    assert "alembic" in command and "upgrade" in command
    healthcheck = migrate.get("healthcheck") or {}
    assert healthcheck.get("disable") is True
    for name in MIGRATE_DEPENDENTS:
        depends = services[name].get("depends_on") or {}
        dep = depends.get("hub-migrate") if isinstance(depends, dict) else None
        assert dep is not None
        assert dep.get("condition") == "service_completed_successfully"


@pytest.mark.parametrize("name", HUB_PROCESS_HEALTHCHECK_SERVICES)
def test_hub_workers_healthcheck_does_not_probe_api_port(
    services: dict[str, Any], name: str
) -> None:
    healthcheck = services[name].get("healthcheck") or {}
    test = _healthcheck_test(services[name])
    assert "os.kill" in test
    assert "8000" not in test
    assert healthcheck.get("disable") is not True


def test_sidecar_ui_healthchecks(services: dict[str, Any]) -> None:
    assert "healthcheck.js" in _healthcheck_test(services["redis-commander"])
    assert "5555" in _healthcheck_test(services["flower"])
    assert "php" in _healthcheck_test(services["adminer"])
    assert (services["adminer"].get("healthcheck") or {}).get("disable") is not True


def test_otel_collector_probes_health_check_extension(services: dict[str, Any]) -> None:
    dockerfile = Path("infra/otel/Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=busybox" in dockerfile
    assert "/bin/busybox" in dockerfile
    healthcheck = services["otel-collector"].get("healthcheck") or {}
    test = _healthcheck_test(services["otel-collector"])
    assert "/bin/busybox" in test
    assert "13133" in test
    assert healthcheck.get("disable") is not True
