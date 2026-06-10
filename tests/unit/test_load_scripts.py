"""Pin tests for load scripts, Makefile, and locustfile (no Docker)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_makefile_load_locust_targets() -> None:
    makefile = _read("Makefile")
    assert "load-locust:" in makefile
    assert "load-locust-ui:" in makefile
    assert "stack-up:" in makefile
    assert "stack-down:" in makefile
    assert "--remove-orphans" in makefile
    assert "down -v" not in makefile


def test_load_smoke_script_flags() -> None:
    script = _read("scripts/load_smoke.sh")
    assert "--headless" in script
    assert "--exit-code-on-error 1" in script
    assert "--html" in script
    assert "--csv" in script
    assert "python -m loadtests.preflight" in script
    assert "source .env" not in script
    assert "LOAD_LOCUST_OTEL" in script
    assert "locust_args+=(--otel)" in script.replace(" ", "")


def test_load_locust_ui_script() -> None:
    script = _read("scripts/load_locust_ui.sh")
    assert "8089" in script
    assert "source .env" not in script


def test_pyproject_locust_dependency() -> None:
    pyproject = _read("pyproject.toml")
    assert "locust[otel]" in pyproject


def test_locustfile_tasks_and_exit_code() -> None:
    locustfile = _read("loadtests/locustfile.py")
    assert "accept_outbound_event" in locustfile
    assert "inbound_health" in locustfile
    assert "/internal/v1/outbound/events" in locustfile
    assert "process_exit_code" in locustfile
