import pytest

from app.config import Settings, get_settings
from app.observability.metrics import (
    FORBIDDEN_METRIC_ATTRIBUTES,
    HUB_METRIC_NAMES,
    record_delivery_metric,
    validate_metric_attributes,
)
from app.observability.otel import build_resource, configure_otel, shutdown_otel


@pytest.fixture(autouse=True)
def _otel_isolation() -> None:
    shutdown_otel()
    get_settings.cache_clear()
    yield
    shutdown_otel()
    get_settings.cache_clear()


def test_build_resource_has_required_attributes() -> None:
    settings = Settings(
        _env_file=None,
        otel_service_name="hub-api",
        app_version="0.1.0",
        deployment_environment="test",
    )
    resource = build_resource("hub-api", settings)
    attrs = resource.attributes
    assert attrs["service.name"] == "hub-api"
    assert attrs["service.version"] == "0.1.0"
    assert attrs["deployment.environment"] == "test"


def test_configure_otel_noop_when_sdk_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    set_tracer_calls: list[object] = []
    set_meter_calls: list[object] = []

    monkeypatch.setattr(
        "app.observability.otel.trace.set_tracer_provider",
        lambda provider: set_tracer_calls.append(provider),
    )
    monkeypatch.setattr(
        "app.observability.otel.metrics.set_meter_provider",
        lambda provider: set_meter_calls.append(provider),
    )
    settings = Settings(_env_file=None, otel_sdk_disabled=True)
    configure_otel("hub-api", settings)
    assert set_tracer_calls == []
    assert set_meter_calls == []


def test_configure_otel_sets_providers_when_enabled() -> None:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    settings = Settings(
        _env_file=None,
        otel_sdk_disabled=False,
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    configure_otel("hub-api", settings)
    try:
        assert isinstance(trace.get_tracer_provider(), TracerProvider)
        assert isinstance(metrics.get_meter_provider(), MeterProvider)
    finally:
        shutdown_otel()


def test_hub_metric_names_include_required_instruments() -> None:
    assert "hub_outbox_discrepancy_total" not in HUB_METRIC_NAMES
    assert "hub_invalid_transition_total" in HUB_METRIC_NAMES
    assert "hub_deliveries_total" in HUB_METRIC_NAMES
    assert "hub_outbox_unpublished" in HUB_METRIC_NAMES


@pytest.mark.parametrize(
    "forbidden_key",
    sorted(FORBIDDEN_METRIC_ATTRIBUTES),
)
def test_validate_metric_attributes_rejects_high_cardinality_keys(forbidden_key: str) -> None:
    with pytest.raises(ValueError, match=forbidden_key):
        validate_metric_attributes({forbidden_key: "value", "partner_slug": "acme"})


def test_validate_metric_attributes_allows_partner_slug() -> None:
    attrs = validate_metric_attributes(
        {"partner_slug": "acme", "status": "delivered", "event_type": "order.created"},
    )
    assert attrs == {
        "partner_slug": "acme",
        "status": "delivered",
        "event_type": "order.created",
    }


def test_record_delivery_metric_rejects_delivery_id() -> None:
    with pytest.raises(ValueError, match="delivery_id"):
        record_delivery_metric(
            "hub_deliveries_total",
            attributes={"delivery_id": "0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c"},
        )


def test_default_otel_service_name_is_hub_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    settings = Settings(_env_file=None)
    assert settings.otel_service_name == "hub-api"
