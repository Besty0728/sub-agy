"""End-to-end tests using fake agy."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest


def wait_for_state(run_bridge, job_id: str, target: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run_bridge("status", job_id, check=False)
        data = json.loads(result.stdout)
        if data.get("state") == target:
            return data
        time.sleep(0.2)
    raise TimeoutError(f"job {job_id} did not reach {target}")


def test_run_status_result_cleanup(git_repo: Path, run_bridge) -> None:
    structured = json.dumps(
        {
            "summary": "created hello.txt",
            "files_changed": ["hello.txt"],
            "tests_ran": [],
            "tests_passed": True,
        }
    )
    env = {
        "FAKE_AGY_STRUCTURED": structured,
        "FAKE_AGY_RESPONSE": "created file",
    }
    plan = git_repo / "plan.md"
    plan.write_text(
        '---\nscope: ["*"]\nacceptance:\n  - create hello.txt\n---\nCreate hello.txt.\n',
        encoding="utf-8",
    )

    result = run_bridge("run", "--plan", str(plan), env=env)
    assert result.returncode == 0
    data = json.loads(result.stdout)
    job_id = data["job_id"]
    assert data["worktree"] is not None
    assert data["branch"].startswith("agy/")

    status = wait_for_state(run_bridge, job_id, "done")
    assert status["state"] == "done"

    result = run_bridge("result", job_id)
    payload = json.loads(result.stdout)
    assert payload["contract_ok"] is True
    assert payload["summary"] == "created hello.txt"
    assert "hello.txt" in payload["files_changed_git"]

    # cleanup
    cleanup = run_bridge("cleanup", "--purge", "--delete-branch", job_id)
    assert cleanup.returncode == 0
    assert not (git_repo / ".subagy" / "jobs" / job_id).exists()


def test_run_concurrency_limit(git_repo: Path, run_bridge, tmp_path: Path) -> None:
    from sub_agy.jobs import write_meta, new_meta

    # Fill the concurrency slot with a live pid so they are not reconciled away.
    for i in range(3):
        meta = new_meta(
            f"j-running-{i}", git_repo, None, None, "sha", "m", "low", "30m"
        )
        meta["state"] = "running"
        meta["pid_supervisor"] = os.getpid()
        write_meta(git_repo, f"j-running-{i}", meta)

    config_path = tmp_path / "config2.toml"
    config_path.write_text(
        'agy_bin = "/bin/true"\nmax_concurrent = 3\n',
        encoding="utf-8",
    )
    env = {"SUB_AGY_CONFIG": str(config_path)}
    plan = git_repo / "plan.md"
    plan.write_text("do work\n", encoding="utf-8")
    result = run_bridge("run", "--plan", str(plan), env=env, check=False)
    assert result.returncode == 5
    assert "max concurrent" in result.stdout


def test_run_no_worktree(git_repo: Path, run_bridge) -> None:
    env = {"FAKE_AGY_RESPONSE": "no worktree"}
    result = run_bridge("run", "--text", "hello", "--no-worktree", env=env)
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["worktree"] is None
    assert data["branch"] is None


def test_feedback_round(git_repo: Path, run_bridge) -> None:
    structured = json.dumps(
        {
            "summary": "first",
            "files_changed": ["a.txt"],
            "tests_passed": True,
        }
    )
    env = {"FAKE_AGY_STRUCTURED": structured}
    result = run_bridge("run", "--text", "do first", env=env)
    job_id = json.loads(result.stdout)["job_id"]
    wait_for_state(run_bridge, job_id, "done")

    # feedback spawns round 2
    result = run_bridge("feedback", job_id, "do second")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["round"] == 2


def test_cancel_running_job(git_repo: Path, run_bridge) -> None:
    env = {"FAKE_AGY_DELAY": "30"}
    result = run_bridge("run", "--text", "slow", env=env, check=False)
    data = json.loads(result.stdout)
    job_id = data["job_id"]

    # give supervisor a moment to spawn agy
    time.sleep(0.5)
    cancel = run_bridge("cancel", job_id, check=False)
    assert cancel.returncode == 0
    # status should become cancelled or already gone
    status = run_bridge("status", job_id, check=False)
    assert json.loads(status.stdout)["state"] in ("cancelled",)
