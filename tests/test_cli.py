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
