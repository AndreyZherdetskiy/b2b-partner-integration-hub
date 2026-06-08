from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_perf_compose_sets_uvicorn_workers() -> None:
    text = (ROOT / "docker-compose.perf.yml").read_text(encoding="utf-8")
    assert "hub-api" in text
    assert "--workers" in text
    assert "4" in text
    assert "OTEL_SDK_DISABLED" in text


def test_makefile_perf_up_scales_consumers() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "perf-up:" in makefile
    block = makefile.split("perf-up:")[1].split("\n\n")[0]
    assert "docker-compose.perf.yml" in block
    assert "hub-outbound-worker=2" in block
    assert "hub-outbox-relay=2" in block
    assert "LOAD_LOCUST_OTEL=1" not in block


def test_stack_down_includes_perf_overlay() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    down = makefile.split("stack-down:")[1].split("\n\n")[0]
    assert "docker-compose.perf.yml" in down
    assert " -v" not in down.replace("--remove-orphans", "")


def test_perf_overlay_raises_api_cpu_quota_not_default_stack() -> None:
    overlay = (ROOT / "docker-compose.perf.yml").read_text(encoding="utf-8")
    base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "cpus:" in overlay
    assert '"4.0"' in overlay or "4.0" in overlay
    assert 'cpus: "1.0"' in base
