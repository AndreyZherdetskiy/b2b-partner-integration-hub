"""Pin Locust OTEL + k6 Grafana script contracts (no live Grafana)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_prometheus_compose_enables_remote_write_receiver() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "--web.enable-remote-write-receiver" in text
    assert "9090:9090" in text


def test_load_smoke_otel_is_opt_in() -> None:
    text = (ROOT / "scripts" / "load_smoke.sh").read_text(encoding="utf-8")
    assert "LOAD_LOCUST_OTEL" in text
    assert "--otel" in text
    assert "OTEL_EXPORTER_OTLP_PROTOCOL" in text
    assert "http/protobuf" in text
    assert "http://127.0.0.1:4318" in text
    assert "docker network inspect" in text
    assert "b2b-partner-integration-hub" in text
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    default_block = makefile.split("load-locust:")[1].split("load-locust-ui:")[0]
    assert "LOAD_LOCUST_OTEL=1" not in default_block
    assert "load-locust-otel:" in makefile
    otel_block = makefile.split("load-locust-otel:")[1].split("load-k6-grafana:")[0]
    assert "LOAD_LOCUST_OTEL=1" in otel_block


def test_load_k6_grafana_uses_compose_dns_and_stdin() -> None:
    text = (ROOT / "scripts" / "load_k6_grafana.sh").read_text(encoding="utf-8")
    assert "b2b-partner-integration-hub" in text
    assert "docker network inspect" in text
    assert "experimental-prometheus-rw" in text
    assert "http://prometheus:9090/api/v1/write" in text
    assert "http://hub-api:8000" in text
    assert "run -" in text
    assert "--no-thresholds" not in text
    assert "outbound_ingest.js" in text
    assert "ADMIN_TOKEN" in text
    assert "K6_PARTNER_PUBLIC_ID" in text


def test_k6_dashboard_is_19665_family() -> None:
    path = ROOT / "docs" / "grafana" / "dashboards" / "k6-prometheus.json"
    text = path.read_text(encoding="utf-8")
    assert "k6_" in text
    assert "Prometheus" in text
    assert "locust" not in text.lower()


def test_default_load_locust_makefile_does_not_force_otel() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "load-k6-grafana:" in makefile
