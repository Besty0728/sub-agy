"""Tests for job record helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sub_agy.jobs import (
    count_active,
    elapsed_seconds,
    generate_job_id,
    latest_step_summary,
    list_jobs,
    meta_path,
    new_meta,
    reconcile_state,
    write_meta,
)


def test_generate_job_id_format() -> None:
    jid = generate_job_id()
    assert jid.startswith("j-")
    assert len(jid.split("-")[-1]) == 4


def test_new_meta_defaults() -> None:
    meta = new_meta(
        job_id="j-1",
        project=Path("/p"),
        worktree=None,
        branch=None,
        base_sha="abc",
        model="m",
        effort="low",
        timeout="30m",
    )
    assert meta["state"] == "queued"
    assert meta["base_sha"] == "abc"
    assert meta["worktree"] is None


def test_count_active(git_repo: Path) -> None:
    meta = new_meta(
        "j-1", git_repo, None, None, "sha", "m", "low", "30m"
    )
    meta["state"] = "running"
    meta["pid_supervisor"] = os.getpid()
    write_meta(git_repo, "j-1", meta)
    assert count_active(git_repo) == 1

    meta2 = new_meta(
        "j-2", git_repo, None, None, "sha", "m", "low", "30m"
    )
    meta2["state"] = "done"
    write_meta(git_repo, "j-2", meta2)
    assert count_active(git_repo) == 1


def test_reconcile_interrupted(git_repo: Path) -> None:
    meta = new_meta(
        "j-1", git_repo, None, None, "sha", "m", "low", "30m"
    )
    meta["state"] = "running"
    meta["pid_supervisor"] = 99999999
    reconciled = reconcile_state(meta)
    assert reconciled["state"] == "interrupted"
    assert reconciled["finished_at"] is not None


def test_reconcile_running_still_alive(git_repo: Path) -> None:
    meta = new_meta(
        "j-1", git_repo, None, None, "sha", "m", "low", "30m"
    )
    meta["state"] = "running"
    meta["pid_supervisor"] = os.getpid()
    reconciled = reconcile_state(meta)
    assert reconciled["state"] == "running"


def test_latest_step_summary(git_repo: Path) -> None:
    jdir = git_repo / ".subagy" / "jobs" / "j-1"
    jdir.mkdir(parents=True)
    events = jdir / "events.ndjson"
    events.write_text(
        '{"type":"round","n":1}\n{"role":"assistant","content":"hello"}\n{"type":"result","response":"final"}\n',
        encoding="utf-8",
    )
    assert latest_step_summary(git_repo, "j-1") == "final"


def test_list_jobs(git_repo: Path) -> None:
    meta = new_meta(
        "j-1", git_repo, None, None, "sha", "m", "low", "30m"
    )
    meta["state"] = "done"
    write_meta(git_repo, "j-1", meta)
    jobs = list_jobs(git_repo, state_filter="done")
    assert len(jobs) == 1
    assert jobs[0]["id"] == "j-1"


def test_latest_step_summary_real_result_event(git_repo: Path) -> None:
    """latest_step_summary recognises the real agy result envelope."""
    jdir = git_repo / ".subagy" / "jobs" / "j-real"
    jdir.mkdir(parents=True)
    events = jdir / "events.ndjson"
    events.write_text(
        '{"event":"result","result":{"response":"real result","status":"SUCCESS"}}\n',
        encoding="utf-8",
    )
    assert latest_step_summary(git_repo, "j-real") == "real result"


def test_elapsed_seconds(git_repo: Path) -> None:
    from datetime import datetime, timezone

    meta = new_meta(
        "j-1", git_repo, None, None, "sha", "m", "low", "30m"
    )
    meta["started_at"] = datetime.now(timezone.utc).isoformat()
    elapsed = elapsed_seconds(meta)
    assert elapsed is not None
    assert elapsed >= 0
