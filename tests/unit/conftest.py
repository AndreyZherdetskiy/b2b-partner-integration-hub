"""Shared unit-test fixtures."""

from __future__ import annotations

import pytest

from app.domain.services.accept_path_cache import reset_accept_path_cache


@pytest.fixture(autouse=True)
def _reset_accept_path_cache() -> None:
    reset_accept_path_cache()
