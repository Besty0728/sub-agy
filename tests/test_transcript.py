"""Tests for transcript fallback when agy stdout is empty."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sub_agy.jobs import job_dir, read_meta, write_meta
from sub_agy.plan import assemble_prompt, parse_plan
from sub_agy.schema import RESULT_SCHEMA
from sub_agy.supervise import supervise_round
from sub_agy.worktree import git_head


def test_transcript_fallback(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Isolate HOME so we can fake the brain directory.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    brain = home / ".gemini" / "antigravity-cli" / "brain" / "uuid-1" / ".system_generated" / "logs"
    brain.mkdir(parents=True)
    transcript = brain / "transcript.jsonl"
    transcript.write_text(
        '{"role":"user","content":"hi"}\n{"role":"assistant","content":"recovered response"}\n',
        encoding="utf-8",
    )
    # Ensure mtime is recent
    import time

    time.sleep(0.1)

    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\nauto_approve = true\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-fallback"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("hello")
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

    monkeypatch.setenv("FAKE_AGY_NO_RESULT", "1")
    supervise_round(job_id, 1, git_repo)

    meta = read_meta(git_repo, job_id)
    assert meta["recovered_from_transcript"] is True
    result = json.loads((jdir / "result.json").read_text(encoding="utf-8"))
    assert result["response_text"] == "recovered response"


def test_transcript_fallback_content_blocks(
    git_repo: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_find_transcript_response extracts text from content-block lists."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    brain = home / ".gemini" / "antigravity-cli" / "brain" / "uuid-blocks" / ".system_generated" / "logs"
    brain.mkdir(parents=True)
    transcript = brain / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"role": "assistant", "content": [{"type": "text", "text": "first"}]})
        + "\n"
        + json.dumps(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "second block"},
                    {"type": "text", "text": "third block"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    import time

    time.sleep(0.1)

    config_path = git_repo / "test-config.toml"
    config_path.write_text(f'agy_bin = "{fake_agy}"\nauto_approve = true\n', encoding="utf-8")
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))

    job_id = "j-blocks"
    jdir = job_dir(git_repo, job_id)
    jdir.mkdir(parents=True)
    plan = parse_plan("hello")
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

    monkeypatch.setenv("FAKE_AGY_NO_RESULT", "1")
    supervise_round(job_id, 1, git_repo)

    result = json.loads((jdir / "result.json").read_text(encoding="utf-8"))
    assert result["response_text"] == "second block\nthird block"
