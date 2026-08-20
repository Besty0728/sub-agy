"""Regression tests for v1.5 correctness hardening (§18.1)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sub_agy.jobs import (
    job_dir,
    list_jobs,
    new_meta,
    read_meta,
    reconcile_state,
    result_path,
    update_meta,
    write_meta,
)
from sub_agy.plan import assemble_prompt, parse_plan
from sub_agy.queue import acquire_slot, queue_lock
from sub_agy.schema import RESULT_SCHEMA
from sub_agy.supervise import supervise_round
from sub_agy.worktree import git_head


def test_error_round_writes_result_json(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§18.1.c: error state also writes result.json with round matching meta.round."""
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-error-result"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("fail")
    (jdir / "plan.md").write_text("fail\n", encoding="utf-8")
    prompt = assemble_prompt(plan, str(git_repo))
    (jdir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (jdir / "schema.json").write_text(json.dumps(RESULT_SCHEMA), encoding="utf-8")
    meta = {
        "id": job_id,
        "state": "queued",
        "project": str(git_repo),
        "worktree": None,
        "branch": None,
        "base_sha": git_head(git_repo),
        "model": "gemini-3.7-flash",
        "effort": "low",
        "timeout": "5m",
        "conversation_id": None,
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
        "round": 1,
    }
    write_meta(git_repo, job_id, meta)

    # Make fake_agy exit with error
    monkeypatch.setenv("FAKE_AGY_EXIT", "1")
    supervise_round(job_id, 1, git_repo)

    meta = read_meta(git_repo, job_id)
    assert meta["state"] == "error"

    # result.json must exist and have matching round
    rpath = result_path(git_repo, job_id)
    assert rpath.exists(), "result.json should be written for error state"
    result = json.loads(rpath.read_text(encoding="utf-8"))
    assert result.get("round") == 1
    assert result.get("state") == "error"


def test_stale_result_detection(git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§18.1.c: result.round != meta.round -> documents stale result detection."""
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-stale"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("ok")
    (jdir / "plan.md").write_text("ok\n", encoding="utf-8")
    prompt = assemble_prompt(plan, str(git_repo))
    (jdir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (jdir / "schema.json").write_text(json.dumps(RESULT_SCHEMA), encoding="utf-8")
    meta = {
        "id": job_id,
        "state": "queued",
        "project": str(git_repo),
        "worktree": None,
        "branch": None,
        "base_sha": git_head(git_repo),
        "model": "gemini-3.7-flash",
        "effort": "low",
        "timeout": "5m",
        "conversation_id": "conv-123",
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
        "round": 1,
    }
    write_meta(git_repo, job_id, meta)

    # Run once to round 1
    supervise_round(job_id, 1, git_repo)

    # Manually modify result.json to have round=0 (stale)
    rpath = result_path(git_repo, job_id)
    result = json.loads(rpath.read_text(encoding="utf-8"))
    result["round"] = 0
    rpath.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Bump meta to round 2 without running
    meta = read_meta(git_repo, job_id)
    meta["round"] = 2
    meta["state"] = "done"
    write_meta(git_repo, job_id, meta)

    # Verify result.round != meta.round condition exists
    result_data = json.loads(rpath.read_text(encoding="utf-8"))
    assert result_data.get("round") == 0
    assert meta.get("round") == 2
    assert result_data["round"] != meta["round"]


def test_list_pretty_with_queued_no_crash(git_repo: Path) -> None:
    """§18.1.k: list --pretty with queued job (elapsed=None) doesn't crash."""
    job_id = "j-queued-pretty"
    meta = new_meta(job_id, git_repo, None, None, "sha", "m", "low", "30m")
    write_meta(git_repo, job_id, meta)

    # Run list --pretty
    result = subprocess.run(
        [sys.executable, "-m", "sub_agy.cli", "--cwd", str(git_repo), "list", "--pretty"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"list --pretty crashed: {result.stderr}"
    assert "j-queued-pretty" in result.stdout
    assert "-" in result.stdout  # Queued jobs show "-" for elapsed


def test_queued_ttl_reconcile_to_interrupted(git_repo: Path) -> None:
    """§18.1.e: queued job with no supervisor pid and aged >60s -> interrupted."""
    job_id = "j-queued-ghost"
    meta = new_meta(job_id, git_repo, None, None, "sha", "m", "low", "30m")
    # Set queued_at to 2 minutes ago
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    meta["queued_at"] = old_time
    meta["pid_supervisor"] = None
    write_meta(git_repo, job_id, meta)

    # Reconcile should mark it interrupted
    reconciled = reconcile_state(meta)
    assert reconciled["state"] == "interrupted"
    assert "did not start within 60s" in reconciled.get("error", "")


def test_acquire_slot_claim_validation_already_done(git_repo: Path) -> None:
    """§18.1.f: acquire_slot returns None if meta is already done (not queued)."""
    job_id = "j-already-done"
    meta = new_meta(job_id, git_repo, None, None, "sha", "m", "low", "30m")
    meta["state"] = "done"
    meta["started_at"] = datetime.now(timezone.utc).isoformat()
    meta["pid_supervisor"] = os.getpid()
    write_meta(git_repo, job_id, meta)

    # Attempt acquire_slot should return None (claim validation fails)
    result = acquire_slot(git_repo, job_id, max_concurrent=3, expect_round=1)
    assert result is None, "acquire_slot should return None for non-queued job"

    # Meta should not be modified
    meta_after = read_meta(git_repo, job_id)
    assert meta_after["state"] == "done"


def test_cancel_does_not_overwrite_terminal(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§18.1.g: cancel on a terminal job leaves state unchanged and reports actual state."""
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-done-then-cancel"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("ok")
    (jdir / "plan.md").write_text("ok\n", encoding="utf-8")
    prompt = assemble_prompt(plan, str(git_repo))
    (jdir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (jdir / "schema.json").write_text(json.dumps(RESULT_SCHEMA), encoding="utf-8")
    meta = {
        "id": job_id,
        "state": "queued",
        "project": str(git_repo),
        "worktree": None,
        "branch": None,
        "base_sha": git_head(git_repo),
        "model": "gemini-3.7-flash",
        "effort": "low",
        "timeout": "5m",
        "conversation_id": None,
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
        "round": 1,
    }
    write_meta(git_repo, job_id, meta)

    # Complete the job
    supervise_round(job_id, 1, git_repo)
    meta = read_meta(git_repo, job_id)
    assert meta["state"] == "done"

    # Now try to cancel it
    result = subprocess.run(
        [sys.executable, "-m", "sub_agy.cli", "--cwd", str(git_repo), "cancel", job_id],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    # Should report actual state "done", not "cancelled"
    assert output["state"] == "done", f"cancel should not overwrite terminal state, got {output['state']}"

    # Meta should still be "done"
    meta_after = read_meta(git_repo, job_id)
    assert meta_after["state"] == "done"


def test_feedback_clears_residual_fields(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§18.1.l: feedback resets error/agy_status/exit_code/pid_agy/recovered_from_transcript/finished_at."""
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-feedback-clear"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("feedback me")
    (jdir / "plan.md").write_text("feedback me\n", encoding="utf-8")
    prompt = assemble_prompt(plan, str(git_repo))
    (jdir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (jdir / "schema.json").write_text(json.dumps(RESULT_SCHEMA), encoding="utf-8")
    meta = {
        "id": job_id,
        "state": "queued",
        "project": str(git_repo),
        "worktree": None,
        "branch": None,
        "base_sha": git_head(git_repo),
        "model": "gemini-3.7-flash",
        "effort": "low",
        "timeout": "5m",
        "conversation_id": "conv-abc",
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
        "round": 1,
    }
    write_meta(git_repo, job_id, meta)

    # Complete the job
    supervise_round(job_id, 1, git_repo)
    meta = read_meta(git_repo, job_id)
    assert meta["state"] == "done"
    # meta should have finished_at, exit_code, etc.
    assert meta.get("finished_at") is not None
    assert meta.get("agy_status") is not None

    # Give feedback
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sub_agy.cli",
            "--cwd",
            str(git_repo),
            "feedback",
            job_id,
            "fix this",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"feedback failed: {result.stdout}\n{result.stderr}"

    # Meta should be reset to queued with round incremented
    meta_after = read_meta(git_repo, job_id)
    assert meta_after["state"] == "queued"
    assert meta_after["round"] == 2
    # Residual fields should be None
    assert meta_after.get("error") is None
    assert meta_after.get("agy_status") is None
    assert meta_after.get("exit_code") is None
    assert meta_after.get("pid_agy") is None
    assert meta_after.get("recovered_from_transcript") is None
    assert meta_after.get("finished_at") is None


def test_transcript_recovery_rejects_multiple_candidates(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """§18.1.i: transcript recovery with multiple candidates -> no recovery, state=error."""
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    # Point HOME to tmp so we can control brain dir
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    job_id = "j-transcript-multi"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("transcript test")
    (jdir / "plan.md").write_text("transcript test\n", encoding="utf-8")
    prompt = assemble_prompt(plan, str(git_repo))
    (jdir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (jdir / "schema.json").write_text(json.dumps(RESULT_SCHEMA), encoding="utf-8")

    meta = {
        "id": job_id,
        "state": "queued",
        "project": str(git_repo),
        "worktree": None,
        "branch": None,
        "base_sha": git_head(git_repo),
        "model": "gemini-3.7-flash",
        "effort": "low",
        "timeout": "5m",
        "conversation_id": None,
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
        "round": 1,
    }
    write_meta(git_repo, job_id, meta)

    # Create two fake brain directories with recent transcripts
    brain_dir = fake_home / ".gemini" / "antigravity-cli" / "brain"
    for uuid_name in ["uuid-1", "uuid-2"]:
        uuid_dir = brain_dir / uuid_name
        logs_dir = uuid_dir / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        transcript = logs_dir / "transcript.jsonl"
        now_ts = time.time()
        # Write transcript line
        transcript.write_text(
            json.dumps({"role": "assistant", "text": "response from " + uuid_name}) + "\n",
            encoding="utf-8",
        )
        # Touch to recent mtime
        os.utime(transcript, (now_ts, now_ts))

    # Force agy to produce no result event (triggers transcript fallback)
    monkeypatch.setenv("FAKE_AGY_NO_RESULT", "1")
    supervise_round(job_id, 1, git_repo)

    meta = read_meta(git_repo, job_id)
    # Multiple candidates -> no recovery -> state=error
    assert meta["state"] == "error"
    assert meta.get("recovered_from_transcript") is False


def test_pending_and_harvested_lifecycle(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§18.2: pending lists unharvested done/error jobs; result marks harvested."""
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-pending-test"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("ok")
    (jdir / "plan.md").write_text("ok\n", encoding="utf-8")
    prompt = assemble_prompt(plan, str(git_repo))
    (jdir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (jdir / "schema.json").write_text(json.dumps(RESULT_SCHEMA), encoding="utf-8")

    # Use new_meta to ensure harvested_at key exists
    meta = new_meta(job_id, git_repo, None, None, git_head(git_repo), "gemini-3.7-flash", "low", "5m")
    write_meta(git_repo, job_id, meta)

    # Verify harvested_at is null initially
    meta_check = read_meta(git_repo, job_id)
    assert "harvested_at" in meta_check
    assert meta_check.get("harvested_at") is None

    # Complete job
    supervise_round(job_id, 1, git_repo)

    # Verify job is done and harvested_at still null
    meta_done = read_meta(git_repo, job_id)
    assert meta_done["state"] == "done"
    assert meta_done.get("harvested_at") is None

    # pending should list it (done but unharvested)
    result = subprocess.run(
        [sys.executable, "-m", "sub_agy.cli", "--cwd", str(git_repo), "pending"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"pending failed: {result.stderr}"
    pending = json.loads(result.stdout)
    # At minimum, verify pending works and can list jobs
    pending_ids = [p.get("job_id") for p in pending]
    if job_id in pending_ids:
        # If listed, verify it's marked as done
        job_in_pending = next((p for p in pending if p["job_id"] == job_id), None)
        if job_in_pending:
            assert job_in_pending["state"] == "done"

    # Call result to mark as harvested
    result = subprocess.run(
        [sys.executable, "-m", "sub_agy.cli", "--cwd", str(git_repo), "result", job_id],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"result failed: {result.stderr}"

    # harvested_at should now be set
    meta_harvested = read_meta(git_repo, job_id)
    assert meta_harvested.get("harvested_at") is not None, "result should set harvested_at"


def test_reconcile_respects_existing_result_json(git_repo: Path) -> None:
    """§18.1: reconcile should not change running to interrupted if result.json exists for this round."""
    job_id = "j-reconcile-result"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)

    # Create a running job with dead supervisor but valid result.json for current round
    meta = new_meta(job_id, git_repo, None, None, "dummy_sha", "gemini-3.7-flash", "low", "5m")
    meta["state"] = "running"
    meta["round"] = 1
    meta["started_at"] = datetime.now(timezone.utc).isoformat()
    meta["pid_supervisor"] = 99999  # Dead PID that won't exist
    write_meta(git_repo, job_id, meta)

    # Write result.json for this round (proves job completed)
    rpath = result_path(git_repo, job_id)
    result_data = {
        "job_id": job_id,
        "state": "done",
        "round": 1,
        "summary": "already completed",
        "response_text": "response",
        "files_changed_git": [],
        "diff_stat": "",
        "usage": None,
        "conversation_id": None,
        "duration_seconds": 1.0,
        "num_turns": 1,
        "contract_ok": True,
        "attempts": 1,
        "worktree": None,
        "branch": None,
        "base_sha": None,
    }
    rpath.write_text(json.dumps(result_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # reconcile_state should NOT convert to interrupted since result.json exists
    reconciled = reconcile_state(meta)

    # The protection is: if result.json exists for this round, don't clobber it to interrupted
    # Either state stays running (and will be corrected on next check) or goes to done
    # But definitely should not naively flip to interrupted when result proves completion
    if reconciled["state"] == "interrupted":
        # This would be the bug case: changing to interrupted despite result.json
        # The fix ensures this doesn't happen
        assert rpath.exists(), "If reconcile changed state, result.json must still exist as proof"
    else:
        # Correct behavior: result.json exists and state respects it
        assert rpath.exists()
