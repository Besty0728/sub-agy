"""Tests for watch command and run --wait."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sub_agy.jobs import (
    elapsed_seconds,
    job_dir,
    new_meta,
    result_path,
    write_meta,
)
from sub_agy.watch import _build_job_summary


def _run_watch(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sub_agy.cli", "--cwd", str(project), "watch", *args],
        cwd=str(project),
        capture_output=True,
        text=True,
    )


def _make_meta(project: Path, job_id: str, state: str = "running") -> dict:
    meta = new_meta(job_id, project, None, None, "sha", "m", "low", "30m")
    meta["state"] = state
    if state == "running":
        meta["started_at"] = meta["created_at"]
        meta["pid_supervisor"] = os.getpid()
    return meta


def test_watch_done_job(tmp_project: Path) -> None:
    job_id = "j-done"
    meta = _make_meta(tmp_project, job_id, "running")
    write_meta(tmp_project, job_id, meta)

    def finish_later() -> None:
        time.sleep(0.2)
        meta["state"] = "done"
        meta["finished_at"] = meta["created_at"]
        meta["agy_status"] = "SUCCESS"
        write_meta(tmp_project, job_id, meta)
        result_path(tmp_project, job_id).write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "state": "done",
                    "agy_status": "SUCCESS",
                    "round": 2,
                    "summary": "all good",
                    "contract_ok": True,
                    "structured_output": {"tests_passed": True},
                    "diff_stat": "1 file changed",
                }
            ),
            encoding="utf-8",
        )

    thread = threading.Thread(target=finish_later, daemon=True)
    thread.start()

    result = _run_watch(tmp_project, job_id, "--interval", "0.5", "--timeout", "5s")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data, list) and len(data) == 1
    item = data[0]
    assert item["job_id"] == job_id
    assert item["state"] == "done"
    assert item["round"] == 2
    assert item["agy_status"] == "SUCCESS"
    assert item["summary"] == "all good"
    assert item["contract_ok"] is True
    assert item["tests_passed"] is True
    assert item["diff_stat"] == "1 file changed"
    assert item["result_path"].endswith(f"jobs/{job_id}/result.json")
    assert item["events_path"].endswith(f"jobs/{job_id}/events.ndjson")
    assert item["elapsed_seconds"] is not None
    assert item["elapsed_seconds"] >= 0


def test_watch_missing_job(tmp_project: Path) -> None:
    result = _run_watch(tmp_project, "j-no-such", "--interval", "0.5", "--timeout", "1s")
    assert result.returncode == 3
    assert "not found" in result.stderr.lower()


def test_watch_timeout_keeps_status(tmp_project: Path) -> None:
    job_id = "j-running"
    meta = _make_meta(tmp_project, job_id, "running")
    write_meta(tmp_project, job_id, meta)

    start = time.time()
    result = _run_watch(tmp_project, job_id, "--interval", "0.5", "--timeout", "1s")
    elapsed = time.time() - start

    assert result.returncode == 124, result.stderr
    # Should have waited at least the timeout, but not much more.
    assert 0.8 <= elapsed <= 3.0
    data = json.loads(result.stdout)
    assert data[0]["state"] == "running"
    assert data[0]["job_id"] == job_id


def test_watch_mixed_terminal_exit_code(tmp_project: Path) -> None:
    done_id = "j-done"
    err_id = "j-error"

    done_meta = _make_meta(tmp_project, done_id, "done")
    done_meta["agy_status"] = "SUCCESS"
    write_meta(tmp_project, done_id, done_meta)
    result_path(tmp_project, done_id).write_text(
        json.dumps({"job_id": done_id, "state": "done", "contract_ok": True}),
        encoding="utf-8",
    )

    err_meta = _make_meta(tmp_project, err_id, "error")
    err_meta["agy_status"] = "ERROR"
    write_meta(tmp_project, err_id, err_meta)

    result = _run_watch(tmp_project, done_id, err_id, "--interval", "0.5", "--timeout", "5s")
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    by_id = {item["job_id"]: item for item in data}
    assert by_id[done_id]["state"] == "done"
    assert by_id[err_id]["state"] == "error"


def test_watch_pretty_output(tmp_project: Path) -> None:
    job_id = "j-pretty"
    meta = _make_meta(tmp_project, job_id, "done")
    meta["agy_status"] = "SUCCESS"
    write_meta(tmp_project, job_id, meta)
    result_path(tmp_project, job_id).write_text(
        json.dumps({"job_id": job_id, "state": "done", "summary": "x" * 80}),
        encoding="utf-8",
    )
    result = _run_watch(tmp_project, job_id, "--pretty")
    assert result.returncode == 0, result.stderr
    assert job_id in result.stdout
    assert "done" in result.stdout
    # summary should be truncated to 60 chars in pretty mode
    assert "x" * 61 not in result.stdout


def test_build_job_summary_fallbacks(tmp_project: Path) -> None:
    job_id = "j-fallback"
    meta = _make_meta(tmp_project, job_id, "done")
    meta["worktree"] = "/tmp/wt"
    meta["branch"] = "agy/j-fallback"
    meta["round"] = 3
    write_meta(tmp_project, job_id, meta)
    summary = _build_job_summary(tmp_project, job_id, meta)
    assert summary["state"] == "done"
    assert summary["round"] == 3
    assert summary["worktree"] == "/tmp/wt"
    assert summary["branch"] == "agy/j-fallback"
    assert summary["diff_stat"] == ""
    assert summary["contract_ok"] is False


def test_run_wait_end_to_end(git_repo: Path, run_bridge) -> None:
    structured = json.dumps(
        {
            "summary": "run wait ok",
            "files_changed": ["foo.txt"],
            "tests_ran": [],
            "tests_passed": True,
        }
    )
    env = {"FAKE_AGY_STRUCTURED": structured, "FAKE_AGY_RESPONSE": "ok"}
    plan = git_repo / "plan.md"
    plan.write_text("do work\n", encoding="utf-8")

    result = run_bridge("run", "--plan", str(plan), "--wait", env=env, check=False)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data[0]["state"] == "done"
    assert data[0]["contract_ok"] is True
    assert data[0]["summary"] == "run wait ok"
