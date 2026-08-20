"""Shared fixtures for sub-agy tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture
def git_repo(tmp_project: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_project, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_project,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_project,
        check=True,
        capture_output=True,
    )
    (tmp_project / "README.md").write_text("# init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_project,
        check=True,
        capture_output=True,
    )
    return tmp_project


@pytest.fixture
def fake_agy(tmp_path: Path) -> Path:
    """Create a fake agy script that emits a stream-json result event.

    Behaviour is controlled by environment variables:
      - FAKE_AGY_STATUS: result status (default SUCCESS)
      - FAKE_AGY_RESPONSE: response text
      - FAKE_AGY_STRUCTURED: JSON structured_output
      - FAKE_AGY_EXIT: process exit code (default 0)
      - FAKE_AGY_DELAY: sleep seconds before output
      - FAKE_AGY_NO_RESULT: if set, do not emit a result event
      - FAKE_AGY_CONVERSATION: conversation_id to echo
    """
    script = tmp_path / "fake_agy.py"
    script.write_text(
        r"""#!/usr/bin/env python3
import json
import os
import sys
import time

status = os.environ.get("FAKE_AGY_STATUS", "SUCCESS")
response = os.environ.get("FAKE_AGY_RESPONSE", "ok")
structured_raw = os.environ.get("FAKE_AGY_STRUCTURED")
exit_code = int(os.environ.get("FAKE_AGY_EXIT", "0"))
delay = float(os.environ.get("FAKE_AGY_DELAY", "0"))
conversation_id = os.environ.get("FAKE_AGY_CONVERSATION", "conv-123")

if delay:
    time.sleep(delay)

if not os.environ.get("FAKE_AGY_NO_RESULT"):
    event = {
        "type": "result",
        "status": status,
        "response": response,
        "conversation_id": conversation_id,
        "duration_seconds": 1.0,
        "num_turns": 1,
    }
    if structured_raw:
        event["structured_output"] = json.loads(structured_raw)
    print(json.dumps(event))

sys.exit(exit_code)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@pytest.fixture
def config_env(tmp_path: Path, fake_agy: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a config file pointing to fake_agy and set SUB_AGY_CONFIG env var."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'agy_bin = "{fake_agy}"\ndefault_timeout = "5m"\nauto_approve = true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SUB_AGY_CONFIG", str(config_path))
    return config_path


@pytest.fixture
def run_bridge(git_repo: Path, config_env: Path) -> callable:
    """Helper to run sub-agy CLI in the tmp git repo."""

    def _run(*args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
        env = {**(env or {}), "SUB_AGY_CONFIG": str(config_env)}
        return subprocess.run(
            [sys.executable, "-m", "sub_agy.cli", "--cwd", str(git_repo), *args],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
            env=env,
            check=check,
        )

    return _run
