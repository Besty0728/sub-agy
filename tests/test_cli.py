"""Tests for CLI argument handling and basic flag compatibility."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sub_agy.cli", "--cwd", str(cwd), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _make_job(project: Path, job_id: str, state: str = "done") -> None:
    from sub_agy.jobs import job_dir, new_meta, write_meta, result_path

    meta = new_meta(job_id, project, None, None, "base-sha", "m", "low", "30m")
    meta["state"] = state
    write_meta(project, job_id, meta)
    result_path(project, job_id).write_text(
        json.dumps({"job_id": job_id, "state": state, "contract_ok": True}),
        encoding="utf-8",
    )


def test_status_all_json_flag(tmp_project: Path) -> None:
    result = _run_cli(tmp_project, "status", "--all", "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data, list)


def test_list_json_flag(tmp_project: Path) -> None:
    result = _run_cli(tmp_project, "list", "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data, list)


def test_result_json_flag(tmp_project: Path) -> None:
    _make_job(tmp_project, "job-001")
    result = _run_cli(tmp_project, "result", "job-001", "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["job_id"] == "job-001"


def test_fmt_tokens() -> None:
    from sub_agy.schema import fmt_tokens

    assert fmt_tokens(None) == "-"
    assert fmt_tokens(0) == "0"
    assert fmt_tokens(500) == "500"
    assert fmt_tokens(999) == "999"
    assert fmt_tokens(1000) == "1.0k"
    assert fmt_tokens(448266) == "448.3k"
    assert fmt_tokens(1000000) == "1.0M"
    assert fmt_tokens(1234567) == "1.2M"


def test_fmt_elapsed() -> None:
    from sub_agy.schema import fmt_elapsed

    assert fmt_elapsed(None) == "-"
    assert fmt_elapsed(0) == "0:00"
    assert fmt_elapsed(5) == "0:05"
    assert fmt_elapsed(59.4) == "0:59"
    assert fmt_elapsed(65.2) == "1:05"
    assert fmt_elapsed(313.6) == "5:14"
    assert fmt_elapsed(3600) == "1:00:00"
    assert fmt_elapsed(3725) == "1:02:05"


def test_extract_tokens() -> None:
    from sub_agy.schema import extract_tokens

    assert extract_tokens(None) is None
    assert extract_tokens("bad") is None
    assert extract_tokens({}) == {"input": None, "output": None, "total": None}
    assert extract_tokens({
        "input_tokens": 400000,
        "output_tokens": 48266,
        "total_tokens": 448266,
    }) == {
        "input": 400000,
        "output": 48266,
        "total": 448266,
    }


def test_status_tokens_in_json_and_pretty(tmp_project: Path) -> None:
    from sub_agy.jobs import new_meta, result_path, write_meta

    job_id = "j-stat-tokens"
    meta = new_meta(job_id, tmp_project, None, None, "base-sha", "m", "low", "30m")
    meta["state"] = "done"
    write_meta(tmp_project, job_id, meta)

    result_path(tmp_project, job_id).write_text(
        json.dumps({
            "job_id": job_id,
            "state": "done",
            "contract_ok": True,
            "usage": {
                "input_tokens": 400000,
                "output_tokens": 48266,
                "total_tokens": 448266,
            },
        }),
        encoding="utf-8",
    )

    # Test single job JSON
    res = _run_cli(tmp_project, "status", job_id, "--json")
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["tokens"] == {"input": 400000, "output": 48266, "total": 448266}
    assert "elapsed" in data

    # Test --all JSON
    res_all = _run_cli(tmp_project, "status", "--all", "--json")
    assert res_all.returncode == 0, res_all.stderr
    data_all = json.loads(res_all.stdout)
    assert len(data_all) == 1
    assert data_all[0]["tokens"] == {"input": 400000, "output": 48266, "total": 448266}

    # Test --all --pretty table
    res_pretty = _run_cli(tmp_project, "status", "--all", "--pretty")
    assert res_pretty.returncode == 0, res_pretty.stderr
    assert "tokens" in res_pretty.stdout.lower()
    assert "elapsed" in res_pretty.stdout.lower()
    assert "448.3k" in res_pretty.stdout


def test_status_null_tokens_when_no_usage(tmp_project: Path) -> None:
    from sub_agy.jobs import new_meta, write_meta

    job_id = "j-no-usage"
    meta = new_meta(job_id, tmp_project, None, None, "base-sha", "m", "low", "30m")
    meta["state"] = "running"
    write_meta(tmp_project, job_id, meta)

    res = _run_cli(tmp_project, "status", job_id, "--json")
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["tokens"] is None


def test_doctor_missing_agy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test doctor with agy not found on PATH."""
    # Create a config pointing to non-existent agy path
    config_path = tmp_path / "config.toml"
    config_path.write_text('agy_bin = "/nonexistent/path/to/agy"\ndefault_timeout = "5m"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    project = tmp_path / "project"
    project.mkdir()

    result = _run_cli(project, "doctor")
    # Should fail because agy is missing
    assert result.returncode != 0, result.stderr

    data = json.loads(result.stdout)
    assert "hints" in data, "JSON should include 'hints' field"
    assert isinstance(data["hints"], list), "hints should be a list"
    assert len(data["hints"]) > 0, "Should have at least one hint for missing agy"
    # Hint should mention OAuth or 登录
    assert any("OAuth" in hint or "登录" in hint for hint in data["hints"]), f"Hints: {data['hints']}"


def test_doctor_hints_empty_when_no_issues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_agy) -> None:
    """Test doctor with no issues returns empty hints."""
    # Create a config pointing to fake agy that works
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\ndefault_timeout = "5m"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))
    monkeypatch.setenv("FAKE_AGY_STATUS", "SUCCESS")
    monkeypatch.setenv("FAKE_AGY_RESPONSE", "agy 1.2.0")
    monkeypatch.setenv("FAKE_AGY_EXIT", "0")

    project = tmp_path / "project"
    project.mkdir()

    result = _run_cli(project, "doctor")
    # May succeed or fail depending on auth, but should have hints field
    data = json.loads(result.stdout)
    assert "hints" in data, "JSON should include 'hints' field"
    # If no issues, hints should be empty
    if data.get("ok"):
        assert data["hints"] == [], f"Expected empty hints when ok=true, got {data['hints']}"

