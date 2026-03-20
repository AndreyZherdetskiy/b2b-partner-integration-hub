"""Thin admin UI (ADR-006): no delivery pipeline logic in browser source."""

from pathlib import Path

import pytest

ADMIN_SRC = Path("admin_ui/src")
FORBIDDEN_IN_SRC = ("hmac", "kafka", "outbox")


def _tsx_files() -> list[Path]:
    return [path for path in ADMIN_SRC.rglob("*") if path.suffix in {".ts", ".tsx"}]


def test_admin_src_has_no_backend_pipeline_logic() -> None:
    hits: list[str] = []
    for path in _tsx_files():
        content = path.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN_IN_SRC:
            if term in content:
                hits.append(f"{path}: {term}")
    assert hits == []


@pytest.mark.parametrize(
    ("relative", "tokens"),
    [
        (
            "components/DeliveryStatusBadge.tsx",
            ("never", "pending", "delivering", "delivered", "retrying", "failed", "replaying"),
        ),
        (
            "components/BreakerStateBadge.tsx",
            ("never", "closed", "open", "half_open", "unknown"),
        ),
        (
            "components/ReplayApprovalStatusBadge.tsx",
            ("never", "pending", "approved", "rejected"),
        ),
    ],
)
def test_status_badge_is_exhaustive(relative: str, tokens: tuple[str, ...]) -> None:
    text = (ADMIN_SRC / relative).read_text(encoding="utf-8")
    missing = [token for token in tokens if token not in text]
    assert missing == []


def test_admin_client_uses_same_origin_admin_api() -> None:
    client = (ADMIN_SRC / "api" / "client.ts").read_text(encoding="utf-8")
    assert 'export const API_BASE = ""' in client
    assert "http://localhost:8000" not in client


def test_admin_nginx_proxies_admin_api() -> None:
    nginx = Path("admin_ui/nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://hub-api:8000/admin/" in nginx


def test_vite_dev_server_proxies_admin_api() -> None:
    vite = Path("admin_ui/vite.config.ts").read_text(encoding="utf-8")
    assert '"/admin": "http://127.0.0.1:8000"' in vite


def test_admin_ui_dockerfile_skips_ipv6_entrypoint_helper() -> None:
    dockerfile = Path("admin_ui/Dockerfile").read_text(encoding="utf-8")
    assert "npm ci" in dockerfile
    assert "10-listen-on-ipv6-by-default.sh" in dockerfile
