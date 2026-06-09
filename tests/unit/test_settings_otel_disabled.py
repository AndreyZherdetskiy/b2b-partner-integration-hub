import pytest

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_empty_otel_sdk_disabled_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "")
    settings = Settings(_env_file=None)
    assert settings.otel_sdk_disabled is False
