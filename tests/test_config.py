"""Tests for configuration loading and timeout parsing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sub_agy.config import Config, load_config, parse_timeout


def test_parse_timeout_valid() -> None:
    assert parse_timeout("90s") == 90
    assert parse_timeout("30m") == 1800
    assert parse_timeout("1h") == 3600


def test_parse_timeout_invalid() -> None:
    with pytest.raises(ValueError):
        parse_timeout("5")
    with pytest.raises(ValueError):
        parse_timeout("5 minutes")
    with pytest.raises(ValueError):
        parse_timeout("1d")


def test_load_config_defaults() -> None:
    # Ensure no env var points to a real file
    os.environ.pop("SUB_AGY_CONFIG", None)
    config = load_config(Path("/nonexistent/config.toml"))
    assert config.default_model == "gemini-3.7-flash"
    assert config.default_timeout == "30m"
    assert config.max_concurrent == 3


def test_load_config_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'default_model = "gemini-2.0"\ndefault_timeout = "1h"\nmax_concurrent = 5\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))
    config = load_config()
    assert config.default_model == "gemini-2.0"
    assert config.default_timeout == "1h"
    assert config.max_concurrent == 5


def test_config_timeout_seconds() -> None:
    config = Config(default_timeout="2m")
    assert config.timeout_seconds() == 120


def test_load_config_ignores_legacy_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "legacy-config.toml"
    config_path.write_text(
        'default_model = "gemini-2.5"\nauto_approve = true\nsandbox = false\nunknown_key = 123\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))
    config = load_config()
    assert config.default_model == "gemini-2.5"
