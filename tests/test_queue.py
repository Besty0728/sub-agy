"""Tests for the FIFO run-slot queue."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sub_agy.jobs import new_meta, read_meta, write_meta
from sub_agy.queue import (
    QueueTimeout,
    acquire_slot,
    count_running,
    queue_forecast,
    queue_position,
    queued_ahead,
)


def _job(project: Path, job_id: str, state: str, queued_at: str | None = None) -> dict:
    meta = new_meta(job_id, project, None, None, "sha", "m", "low", "30m")
    meta["state"] = state
    meta["pid_supervisor"] = os.getpid()
    if queued_at is not None:
        meta["queued_at"] = queued_at
    write_meta(project, job_id, meta)
    return meta


def test_count_running_ignores_queued(git_repo: Path) -> None:
    _job(git_repo, "j-a", "running")
    _job(git_repo, "j-b", "queued")
    _job(git_repo, "j-c", "done")
    assert count_running(git_repo) == 1


def test_queued_ahead_is_fifo_by_queued_at(git_repo: Path) -> None:
    _job(git_repo, "j-late", "queued", queued_at="2026-01-01T00:00:02+00:00")
    _job(git_repo, "j-early", "queued", queued_at="2026-01-01T00:00:01+00:00")
    assert queued_ahead(git_repo, "j-early") == 0
    assert queued_ahead(git_repo, "j-late") == 1


def test_queued_ahead_falls_back_to_created_at(git_repo: Path) -> None:
    """Metas written before queued_at existed still order deterministically."""
    for job_id, created in (("j-x", "2026-01-01T00:00:09+00:00"), ("j-y", "2026-01-01T00:00:03+00:00")):
        meta = new_meta(job_id, git_repo, None, None, "sha", "m", "low", "30m")
        meta["state"] = "queued"
        meta["pid_supervisor"] = os.getpid()
        meta["created_at"] = created
        del meta["queued_at"]
        write_meta(git_repo, job_id, meta)
    assert queued_ahead(git_repo, "j-y") == 0
    assert queued_ahead(git_repo, "j-x") == 1


def test_queue_position_is_one_based(git_repo: Path) -> None:
    _job(git_repo, "j-1", "queued", queued_at="2026-01-01T00:00:01+00:00")
    _job(git_repo, "j-2", "queued", queued_at="2026-01-01T00:00:02+00:00")
    _job(git_repo, "j-run", "running")
    assert queue_position(git_repo, "j-1") == 1
    assert queue_position(git_repo, "j-2") == 2
    assert queue_position(git_repo, "j-run") is None
    assert queue_position(git_repo, "j-missing") is None


def test_queue_forecast_hides_phantom_wait(git_repo: Path) -> None:
    """A job queued with a slot free reports no wait, not a phantom position 1."""
    _job(git_repo, "j-me", "queued")
    assert queue_position(git_repo, "j-me") == 1
    assert queue_forecast(git_repo, "j-me", max_concurrent=3) is None


def test_queue_forecast_reports_real_wait(git_repo: Path) -> None:
    for i in range(2):
        _job(git_repo, f"j-run-{i}", "running")
    _job(git_repo, "j-first", "queued", queued_at="2026-01-01T00:00:01+00:00")
    _job(git_repo, "j-second", "queued", queued_at="2026-01-01T00:00:02+00:00")
    assert queue_forecast(git_repo, "j-first", max_concurrent=2) == 1
    assert queue_forecast(git_repo, "j-second", max_concurrent=2) == 2
    # One slot free: the head of the queue starts now, the next one still waits.
    assert queue_forecast(git_repo, "j-first", max_concurrent=3) is None
    assert queue_forecast(git_repo, "j-second", max_concurrent=3) == 2


def test_acquire_slot_claims_free_slot(git_repo: Path) -> None:
    _job(git_repo, "j-me", "queued")
    meta = acquire_slot(git_repo, "j-me", max_concurrent=3, poll_interval=0.05)
    assert meta is not None
    assert meta["state"] == "running"
    assert meta["started_at"] is not None
    assert read_meta(git_repo, "j-me")["state"] == "running"


def test_acquire_slot_waits_when_full(git_repo: Path) -> None:
    for i in range(2):
        _job(git_repo, f"j-run-{i}", "running")
    _job(git_repo, "j-me", "queued")
    with pytest.raises(QueueTimeout):
        acquire_slot(git_repo, "j-me", max_concurrent=2, poll_interval=0.05, timeout=0.2)
    assert read_meta(git_repo, "j-me")["state"] == "queued"


def test_acquire_slot_respects_fifo_order(git_repo: Path) -> None:
    """One free slot goes to the earliest-queued job, not whoever polls first."""
    _job(git_repo, "j-run", "running")
    _job(git_repo, "j-early", "queued", queued_at="2026-01-01T00:00:01+00:00")
    _job(git_repo, "j-late", "queued", queued_at="2026-01-01T00:00:02+00:00")

    with pytest.raises(QueueTimeout):
        acquire_slot(git_repo, "j-late", max_concurrent=2, poll_interval=0.05, timeout=0.2)

    meta = acquire_slot(git_repo, "j-early", max_concurrent=2, poll_interval=0.05, timeout=1.0)
    assert meta is not None and meta["state"] == "running"


def test_acquire_slot_fills_multiple_free_slots(git_repo: Path) -> None:
    _job(git_repo, "j-early", "queued", queued_at="2026-01-01T00:00:01+00:00")
    _job(git_repo, "j-late", "queued", queued_at="2026-01-01T00:00:02+00:00")
    # Two slots free, so the second-in-line may start too.
    meta = acquire_slot(git_repo, "j-late", max_concurrent=2, poll_interval=0.05, timeout=1.0)
    assert meta is not None and meta["state"] == "running"


def test_acquire_slot_aborts_on_cancel(git_repo: Path) -> None:
    _job(git_repo, "j-run", "running")
    _job(git_repo, "j-me", "queued")
    assert (
        acquire_slot(
            git_repo,
            "j-me",
            max_concurrent=1,
            poll_interval=0.05,
            should_abort=lambda: True,
        )
        is None
    )
    assert read_meta(git_repo, "j-me")["state"] == "queued"


def test_dead_queued_supervisor_does_not_block_fifo(git_repo: Path) -> None:
    """A queued job whose supervisor died is reconciled, freeing the queue head."""
    dead = new_meta("j-dead", git_repo, None, None, "sha", "m", "low", "30m")
    dead["state"] = "queued"
    dead["queued_at"] = "2026-01-01T00:00:01+00:00"
    dead["pid_supervisor"] = 99999999
    write_meta(git_repo, "j-dead", dead)
    _job(git_repo, "j-run", "running")
    _job(git_repo, "j-me", "queued", queued_at="2026-01-01T00:00:02+00:00")

    meta = acquire_slot(git_repo, "j-me", max_concurrent=2, poll_interval=0.05, timeout=1.0)
    assert meta is not None and meta["state"] == "running"
    assert read_meta(git_repo, "j-dead")["state"] == "interrupted"


def test_on_wait_fires_once(git_repo: Path) -> None:
    _job(git_repo, "j-run", "running")
    _job(git_repo, "j-me", "queued")
    calls: list[tuple[int, int]] = []
    with pytest.raises(QueueTimeout):
        acquire_slot(
            git_repo,
            "j-me",
            max_concurrent=1,
            poll_interval=0.05,
            timeout=0.3,
            on_wait=lambda running, ahead: calls.append((running, ahead)),
        )
    assert calls == [(1, 0)]
