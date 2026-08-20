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
    monkeypatch.setenv("SUB_AGY_CONFIG", str(Path.home()))  # not used directly
    # Point config via meta agy_bin is not stored; fake_agy path must be on PATH or config.
    # Use a dedicated config file for this test.
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\nauto_approve = true\n', encoding="utf-8")
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

    env = {"FAKE_AGY_EXIT": "1", "FAKE_AGY_STATUS": "ERROR"}
    monkeypatch.setenv("FAKE_AGY_EXIT", "1")
    monkeypatch.setenv("FAKE_AGY_STATUS", "ERROR")

    supervise_round(job_id, 1, git_repo)

    meta = read_meta(git_repo, job_id)
    assert meta["state"] == "error"
    assert meta["exit_code"] == 1


def test_supervise_timeout_retry(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\nauto_approve = true\nmax_retries = 1\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-to"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("slow")
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

    monkeypatch.setenv("FAKE_AGY_DELAY", "10")
    monkeypatch.setenv("_SUB_AGY_GRACE_SECONDS", "0")
    supervise_round(job_id, 1, git_repo)

    meta = read_meta(git_repo, job_id)
    # Should have attempted twice (initial + 1 retry) and failed
    assert meta["state"] == "error"
    assert meta["attempts"] == 2
