"""Canonical seed slugs and Grafana PromQL cardinality (partner_slug, not UUID)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.seed_common import (
    CANONICAL_SLUGS,
    canonical_partner_seeds,
    prod_like_extra_seeds,
)

GRAFANA_DIR = Path("docs/grafana/dashboards")
LOAD_DASHBOARD_SKIP = frozenset({"k6-prometheus.json", "locust-otel.json"})


def test_canonical_partner_seeds_match_slug_tuple() -> None:
    slugs = tuple(seed.slug for seed in canonical_partner_seeds())
    assert slugs == CANONICAL_SLUGS


def test_prod_like_catalog_includes_canonical_slugs() -> None:
    slugs = {seed.slug for seed in canonical_partner_seeds()} | {
        seed.slug for seed in prod_like_extra_seeds()
    }
    assert set(CANONICAL_SLUGS) <= slugs
    assert len(prod_like_extra_seeds()) > 0


@pytest.mark.parametrize(
    "dashboard",
    sorted(p for p in GRAFANA_DIR.glob("*.json") if p.name not in LOAD_DASHBOARD_SKIP),
    ids=lambda path: path.name,
)
def test_grafana_dashboard_promql_uses_partner_slug(dashboard: Path) -> None:
    payload = json.loads(dashboard.read_text(encoding="utf-8"))
    exprs = [
        target["expr"]
        for panel in payload.get("panels", [])
        for target in panel.get("targets", [])
        if "expr" in target
    ]
    joined = "\n".join(exprs)
    assert exprs
    assert "partner_slug" in joined
    assert "delivery_id" not in joined
