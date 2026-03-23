"""Pin pre-commit config: Ruff 0.5.7, file hooks, locustfile import (no Docker)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"

STANDARD_HOOKS = (
    "trailing-whitespace",
    "end-of-file-fixer",
    "check-yaml",
    "check-added-large-files",
    "detect-private-key",
)


def _precommit_yaml() -> str:
    assert PRE_COMMIT.is_file(), ".pre-commit-config.yaml must exist"
    return PRE_COMMIT.read_text(encoding="utf-8")


def test_ruff_pre_commit_rev_matches_lockfile() -> None:
    text = _precommit_yaml()
    repo_marker = "repo: https://github.com/astral-sh/ruff-pre-commit"
    assert repo_marker in text
    ruff_block = text.split(repo_marker)[1].split("repo:")[0]
    assert "rev: v0.5.7" in ruff_block


def test_ruff_hook_id_is_ruff_not_ruff_check() -> None:
    text = _precommit_yaml()
    normalized = text.replace("\r\n", "\n")
    assert "      - id: ruff\n" in normalized
    assert "id: ruff-check" not in text


def test_ruff_format_hook_present() -> None:
    text = _precommit_yaml()
    assert "      - id: ruff-format\n" in text.replace("\r\n", "\n")


def test_standard_pre_commit_hooks_present() -> None:
    text = _precommit_yaml()
    assert "pre-commit/pre-commit-hooks" in text
    for hook_id in STANDARD_HOOKS:
        assert f"- id: {hook_id}" in text


def test_local_hook_imports_locustfile() -> None:
    text = _precommit_yaml()
    assert 'uv run --group load python -c "import loadtests.locustfile"' in text
    assert "id: locustfile-import" in text
    assert "files: ^loadtests/" in text


def test_hooks_do_not_use_docker() -> None:
    text = _precommit_yaml()
    assert "docker" not in text.lower()
