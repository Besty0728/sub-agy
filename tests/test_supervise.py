"""Tests for supervise internals and error paths."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sub_agy.jobs import job_dir, read_meta, write_meta
from sub_agy.plan import assemble_prompt, parse_plan
from sub_agy.schema import RESULT_SCHEMA
from sub_agy.supervise import supervise_round
from sub_agy.worktree import git_head


def test_supervise_error_exit(git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-err"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("fail me")
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

    monkeypatch.setenv("FAKE_AGY_EXIT", "1")
    monkeypatch.setenv("FAKE_AGY_STATUS", "ERROR")

    supervise_round(job_id, 1, git_repo)

    meta = read_meta(git_repo, job_id)
    assert meta["state"] == "error"
    assert meta["exit_code"] == 1


def test_supervise_timeout_retry(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that retry budget is correctly distributed: wall_clock = timeout * max_attempts + grace.

    With timeout=1s, max_retries=1 (max_attempts=2), grace=0:
    wall_clock = 1*2 + 0 = 2s total.
    Fake agy delays 1.5s, which exceeds single attempt timeout of 1s,
    so it will timeout and retry. Second attempt also times out.
    Final state should be error with attempts=2.
    """
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\nmax_retries = 1\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-to-retry"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("timeout test")
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
        "timeout": "1s",
        "conversation_id": None,
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
        "round": 1,
    }
    write_meta(git_repo, job_id, meta)

    # Fake agy sleeps 1.5s (exceeds 1s timeout) then exits with error
    # wall_clock = 1s * 2 attempts + 0s grace = 2s total
    # First attempt: timeout=min(1+0, 2)=1s, agy sleeps 1.5s -> timeout
    # Second attempt: timeout=min(1+0, 1~)=1s, agy sleeps 1.5s -> timeout
    # Both attempts exhaust budget -> state=error, attempts=2
    monkeypatch.setenv("FAKE_AGY_DELAY", "1.5")
    monkeypatch.setenv("FAKE_AGY_EXIT", "1")
    monkeypatch.setenv("_SUB_AGY_GRACE_SECONDS", "0")
    supervise_round(job_id, 1, git_repo)

    meta = read_meta(git_repo, job_id)
    assert meta["state"] == "error", f"expected state=error, got {meta['state']}"
    assert meta["attempts"] == 2, f"expected attempts=2, got {meta['attempts']}"


def test_supervise_argv_unconditional_skip_permissions(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\ndefault_timeout = "5m"\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-argv-check"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("argv check")
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

    structured = json.dumps({"summary": "ok", "files_changed": [], "tests_passed": True})
    monkeypatch.setenv("FAKE_AGY_STRUCTURED", structured)
    monkeypatch.setenv("FAKE_AGY_RESPONSE", "done")

    supervise_round(job_id, 1, git_repo)

    stderr_file = jdir / "stderr.log"
    assert stderr_file.exists()
    content = stderr_file.read_text(encoding="utf-8")
    # Parse argv line: --- round 1 argv: <json> ---
    argv_line = None
    for line in content.splitlines():
        if line.startswith("--- round 1 argv:"):
            argv_line = line
            break
    assert argv_line is not None, f"argv line not found in stderr: {content}"
    json_str = argv_line[len("--- round 1 argv:") :].rstrip(" -").strip()
    argv = json.loads(json_str)

    assert "--dangerously-skip-permissions" in argv
    assert "--sandbox" not in argv


def test_supervise_legacy_meta_with_auto_approve(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = git_repo / "test-config.toml"
    config_path.write_text(
        f'agy_bin = "{fake_agy}"\nauto_approve = true\nsandbox = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-legacy-meta"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("legacy meta check")
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
        "auto_approve": True,
        "conversation_id": None,
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
        "round": 1,
    }
    write_meta(git_repo, job_id, meta)

    structured = json.dumps({"summary": "legacy ok", "files_changed": [], "tests_passed": True})
    monkeypatch.setenv("FAKE_AGY_STRUCTURED", structured)
    monkeypatch.setenv("FAKE_AGY_RESPONSE", "done")

    supervise_round(job_id, 1, git_repo)

    meta_read = read_meta(git_repo, job_id)
    assert meta_read["state"] == "done"
    assert meta_read["agy_status"] == "SUCCESS"
