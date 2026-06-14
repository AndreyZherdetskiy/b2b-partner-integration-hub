import pytest
from pydantic import ValidationError

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


def test_empty_admin_bootstrap_token_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_BOOTSTRAP_TOKEN", raising=False)
    settings = Settings(_env_file=None)
    assert settings.admin_bootstrap_token == ""


def test_short_admin_bootstrap_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="32 bytes"):
        Settings(_env_file=None, admin_bootstrap_token="hub_admin")
