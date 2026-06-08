"""Pin GitHub Actions load jobs (no live stack)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def test_existing_jobs_remain() -> None:
    for job in ("lint:", "typecheck:", "test-unit:", "test-contract:", "asyncapi:"):
        assert job in WORKFLOW


def test_python_ci_job_does_not_run_locust_stack() -> None:
    # Existing quality jobs must not call stack-up / load-locust
    lint_block = WORKFLOW.split("lint:")[1].split("typecheck:")[0]
    assert "make stack-up" not in lint_block
    assert "make load-locust" not in lint_block


def test_load_harness_job_exists() -> None:
    assert "load-harness:" in WORKFLOW
    harness = WORKFLOW.split("load-harness:")[1].split("load-locust-smoke:")[0]
    assert "make load-harness" in harness
    assert "make stack-up" not in harness


def test_load_locust_smoke_job() -> None:
    assert "load-locust-smoke:" in WORKFLOW
    smoke = WORKFLOW.split("load-locust-smoke:")[1]
    assert "timeout-minutes:" in smoke
    assert "cp .env.example .env" in smoke
    assert "make stack-up" in smoke
    assert "make load-locust" in smoke
    assert "if: always()" in smoke
    assert "make stack-down" in smoke
    assert "LOAD_LOCUST_OTEL=1" not in smoke
    assert "make load-k6" not in smoke


def test_makefile_load_harness() -> None:
    assert "load-harness:" in MAKEFILE
    block = MAKEFILE.split("load-harness:")[1].split("\n\n")[0]
    assert "--list" in block
    assert "stack-up" not in block
    assert "test_load_helpers.py" in block
