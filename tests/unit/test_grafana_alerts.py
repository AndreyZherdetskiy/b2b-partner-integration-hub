"""Task 11: SLA compliance dashboard panels and Prometheus alert rules."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SLA_DASHBOARD = ROOT / "docs" / "grafana" / "dashboards" / "sla_compliance.json"
ALERTS_FILE = ROOT / "infra" / "prometheus" / "alerts.yml"

_DELIVERY_ID_LABEL = re.compile(r"delivery_id\s*[=~]")
_PARTNER_ID_LABEL = re.compile(r"partner_id\s*[=~]")
_UUID_LABEL_VALUE = re.compile(
    r'[a-zA-Z_]+\s*[=~]\s*"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"',
    re.IGNORECASE,
)

_REQUIRED_ALERTS: dict[str, str] = {
    "HubDLQGrowth": "docs/runbooks/dlq-response.md",
    "HubDLQAge": "docs/runbooks/dlq-response.md",
    "HubComplianceDrop": "docs/runbooks/sla-breach-response.md",
    "HubCircuitOpen": "docs/runbooks/circuit-breaker.md",
}


def _dashboard_exprs(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        target["expr"]
        for panel in payload.get("panels", [])
        for target in panel.get("targets", [])
        if "expr" in target
    ]


def _dashboard_text(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    parts: list[str] = []
    for panel in payload.get("panels", []):
        if panel.get("type") == "text":
            parts.append(panel.get("options", {}).get("content", ""))
    return "\n".join(parts)


def _alert_rules() -> list[dict[str, object]]:
    payload = yaml.safe_load(ALERTS_FILE.read_text(encoding="utf-8"))
    groups = payload.get("groups", [])
    rules: list[dict[str, object]] = []
    for group in groups:
        rules.extend(group.get("rules", []))
    return rules


def test_sla_compliance_dashboard_has_required_panels() -> None:
    payload = json.loads(SLA_DASHBOARD.read_text(encoding="utf-8"))
    assert payload.get("uid") == "hub-sla-compliance"
    assert payload.get("schemaVersion") == 39

    exprs = _dashboard_exprs(SLA_DASHBOARD)
    joined = "\n".join(exprs)
    assert "hub_deliveries_total" in joined
    assert "delivered" in joined
    assert "hub_sla_breaches_total" in joined
    assert "hub_circuit_breaker_state" in joined
    assert 'state="open"' in joined
    assert "hub_dlq_oldest_age_seconds" in joined
    assert "partner_slug" in joined
    assert not _DELIVERY_ID_LABEL.search(joined)

    text = _dashboard_text(SLA_DASHBOARD)
    assert "NaN is not an outage" in text
    assert "docs/runbooks/sla-breach-response.md" in text
    assert "docs/runbooks/dlq-response.md" in text


@pytest.mark.parametrize(
    ("alert_name", "runbook"),
    list(_REQUIRED_ALERTS.items()),
)
def test_prometheus_alert_has_runbook_and_severity(alert_name: str, runbook: str) -> None:
    rules = {rule["alert"]: rule for rule in _alert_rules() if "alert" in rule}
    rule = rules[alert_name]
    annotations = rule.get("annotations", {})
    assert annotations.get("runbook_url") == runbook
    assert annotations.get("summary")
    assert annotations.get("description")
    assert rule.get("labels", {}).get("severity")
    assert rule.get("for")


def test_prometheus_alert_thresholds() -> None:
    by_name = {rule["alert"]: rule for rule in _alert_rules() if "alert" in rule}
    dlq_age = by_name["HubDLQAge"]
    assert "hub_dlq_oldest_age_seconds" in str(dlq_age["expr"])
    assert "3600" in str(dlq_age["expr"])
    assert dlq_age["for"] == "5m"

    compliance = by_name["HubComplianceDrop"]
    assert "hub_sla_compliance_ratio" in str(compliance["expr"])
    assert "0.98" in str(compliance["expr"])
    assert compliance["for"] == "1h"

    circuit = by_name["HubCircuitOpen"]
    assert "hub_circuit_breaker_state" in str(circuit["expr"])
    assert 'state="open"' in str(circuit["expr"])
    assert circuit["for"] == "5m"


def test_alert_exprs_forbid_uuid_label_matchers() -> None:
    rules = _alert_rules()
    for rule in rules:
        expr = str(rule.get("expr", ""))
        name = rule.get("alert", "<unknown>")
        assert not _PARTNER_ID_LABEL.search(expr), f"{name} must not match partner_id label"
        assert not _UUID_LABEL_VALUE.search(expr), f"{name} must not use UUID label matchers"
