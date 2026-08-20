"""Tests for the quota command."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _make_quota_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_agy_quota.py"
    script.write_text(
        f"#!/usr/bin/env python3\n{body}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run_quota(tmp_path: Path, *args: str, agy_bin: Path | None = None) -> subprocess.CompletedProcess:
    env = {}
    if agy_bin is not None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'agy_bin = "{agy_bin}"\n', encoding="utf-8")
        env["SUB_AGY_CONFIG"] = str(config_path)
    return subprocess.run(
        [sys.executable, "-m", "sub_agy.cli", "quota", *args],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def structured_agy(tmp_path: Path) -> Path:
    envelope = {
        "status": "SUCCESS",
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "response": "human readable tsv",
        "command": {
            "data": {
                "groups": [
                    {
                        "name": "Gemini Models",
                        "buckets": [
                            {
                                "id": "gemini-5h",
                                "name": "Five Hour Limit Remaining",
                                "description": "...it will fully refresh in 32 minutes.",
                                "window": "5h",
                                "remaining_fraction": 0.998,
                                "reset_time": "2026-08-20T08:02:31Z",
                            },
                            {
                                "id": "gemini-weekly",
                                "name": "Weekly Limit Remaining",
                                "description": "...it will fully refresh in 6 days, 11 hours.",
                                "window": "weekly",
                                "remaining_fraction": 0.998,
                                "reset_time": "2026-08-26T18:35:04Z",
                            },
                        ],
                    },
                    {
                        "name": "Claude and GPT models",
                        "buckets": [
                            {
                                "id": "claude-weekly",
                                "name": "Weekly Limit Remaining",
                                "description": "no refresh info",
                                "window": "weekly",
                                "remaining_fraction": 1.0,
                                "reset_time": "2026-08-27T00:00:00Z",
                            },
                        ],
                    },
                ]
            }
        },
    }
    body = f"import json, sys\nprint(json.dumps({envelope!r}))\nsys.exit(0)"
    return _make_quota_script(tmp_path, body)


def test_quota_structured(structured_agy: Path, tmp_path: Path) -> None:
    result = _run_quota(tmp_path, agy_bin=structured_agy)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["source"] == "structured"
    assert len(data["groups"]) == 2

    gemini = data["groups"][0]
    assert gemini["name"] == "Gemini Models"
    assert len(gemini["buckets"]) == 2

    five_hour = gemini["buckets"][0]
    assert five_hour["window"] == "5h"
    assert five_hour["remaining_pct"] == 99.8
    assert five_hour["reset_time"] == "2026-08-20T08:02:31Z"
    assert five_hour["reset_in"] == "32 minutes"

    weekly = gemini["buckets"][1]
    assert weekly["window"] == "weekly"
    assert weekly["remaining_pct"] == 99.8
    assert weekly["reset_time"] == "2026-08-26T18:35:04Z"
    assert weekly["reset_in"] == "6 days, 11 hours"

    claude = data["groups"][1]
    assert claude["name"] == "Claude and GPT models"
    assert claude["buckets"][0]["reset_in"] is None


@pytest.fixture
def text_fallback_agy(tmp_path: Path) -> Path:
    envelope = {
        "status": "SUCCESS",
        "usage": {},
        "response": "Gemini Models\tFive Hour Limit Remaining\t99.8%\t2026-08-20T08:02:31Z\n"
        "Gemini Models\tWeekly Limit Remaining\t99.8%\t2026-08-26T18:35:04Z\n",
    }
    body = f"import json, sys\nprint(json.dumps({envelope!r}))\nsys.exit(0)"
    return _make_quota_script(tmp_path, body)


def test_quota_text_fallback(text_fallback_agy: Path, tmp_path: Path) -> None:
    result = _run_quota(tmp_path, agy_bin=text_fallback_agy)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["source"] == "text_fallback"
    assert len(data["groups"]) == 1
    group = data["groups"][0]
    assert group["name"] == "Gemini Models"
    assert len(group["buckets"]) == 2

    five_hour = group["buckets"][0]
    assert five_hour["window"] == "5h"
    assert five_hour["remaining_pct"] == 99.8
    assert five_hour["reset_time"] == "2026-08-20T08:02:31Z"
    assert five_hour["reset_in"] is None

    weekly = group["buckets"][1]
    assert weekly["window"] == "weekly"
    assert weekly["remaining_pct"] == 99.8
    assert weekly["reset_time"] == "2026-08-26T18:35:04Z"


@pytest.fixture
def failing_agy(tmp_path: Path) -> Path:
    body = "import sys\nprint('not json')\nsys.exit(1)"
    return _make_quota_script(tmp_path, body)


def test_quota_failure(failing_agy: Path, tmp_path: Path) -> None:
    result = _run_quota(tmp_path, agy_bin=failing_agy)
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "error" in data


def test_quota_missing_agy(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('agy_bin = "/no/such/agy"\n', encoding="utf-8")
    env = {"SUB_AGY_CONFIG": str(config_path)}
    result = subprocess.run(
        [sys.executable, "-m", "sub_agy.cli", "quota"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 127
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert "error" in data


def test_quota_pretty(structured_agy: Path, tmp_path: Path) -> None:
    result = _run_quota(tmp_path, "--pretty", agy_bin=structured_agy)
    assert result.returncode == 0, result.stderr
    assert "Gemini Models" in result.stdout
    assert "5h" in result.stdout
    assert "weekly" in result.stdout
    assert "5h 窗口用于平滑全球容量" in result.stdout
