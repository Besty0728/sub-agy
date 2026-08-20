"""Job record management and state reconciliation."""

from __future__ import annotations

import json
import os
import random
import string
import time
from datetime import datetime, timezone
from pathlib import Path

from .schema import EXIT_CODES, META_STATES


_JOB_ID_ALPHABET = string.ascii_lowercase + string.digits


def generate_job_id() -> str:
    now = datetime.now(timezone.utc)
    rand = "".join(random.choices(_JOB_ID_ALPHABET, k=4))
    return now.strftime(f"j-%Y%m%d-%H%M%S-") + rand


def project_bridge_dir(project: Path) -> Path:
    return project / ".subagy"


def job_dir(project: Path, job_id: str) -> Path:
    return project_bridge_dir(project) / "jobs" / job_id


def meta_path(project: Path, job_id: str) -> Path:
    return job_dir(project, job_id) / "meta.json"


def result_path(project: Path, job_id: str) -> Path:
    return job_dir(project, job_id) / "result.json"


def events_path(project: Path, job_id: str) -> Path:
    return job_dir(project, job_id) / "events.ndjson"


def stderr_path(project: Path, job_id: str) -> Path:
    return job_dir(project, job_id) / "stderr.log"


def new_meta(
    job_id: str,
    project: Path,
    worktree: Path | None,
    branch: str | None,
    base_sha: str | None,
    model: str,
    effort: str,
    timeout: str,
    auto_approve: bool = False,
) -> dict:
    # SPEC-DEVIATION: SPEC §3 does not list auto_approve in meta.json.  We store
    # it so the detached supervisor process can reconstruct whether to pass
    # --dangerously-skip-permissions without relying on CLI argv forwarding.
    return {
        "id": job_id,
        "state": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "finished_at": None,
        "round": 1,
        "pid_agy": None,
        "pid_supervisor": None,
        "project": str(project.resolve()),
        "worktree": str(worktree.resolve()) if worktree else None,
        "branch": branch,
        "base_sha": base_sha,
        "model": model,
        "effort": effort,
        "timeout": timeout,
        "auto_approve": auto_approve,
        "conversation_id": None,
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
    }


def write_meta(project: Path, job_id: str, meta: dict) -> None:
    path = meta_path(project, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_meta(project: Path, job_id: str) -> dict:
    path = meta_path(project, job_id)
    if not path.exists():
        raise FileNotFoundError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def update_meta(project: Path, job_id: str, **kwargs) -> dict:
    meta = read_meta(project, job_id)
    meta.update(kwargs)
    write_meta(project, job_id, meta)
    return meta


def _is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def reconcile_state(meta: dict) -> dict:
    """Lazily reconcile running jobs whose supervisor has died."""
    if meta.get("state") != "running":
        return meta
    finished_at = meta.get("finished_at")
    supervisor_pid = meta.get("pid_supervisor")
    if finished_at is not None:
        return meta
    if supervisor_pid is not None and _is_running(supervisor_pid):
        return meta
    # Supervisor dead and not finished -> interrupted
    meta["state"] = "interrupted"
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    if meta.get("error") is None:
        meta["error"] = "supervisor process disappeared"
    return meta


def list_jobs(project: Path, state_filter: str | None = None) -> list[dict]:
    jobs: list[dict] = []
    jobs_dir = project_bridge_dir(project) / "jobs"
    if not jobs_dir.exists():
        return jobs
    for entry in sorted(jobs_dir.iterdir()):
        if not entry.is_dir():
            continue
        try:
            meta = read_meta(project, entry.name)
            original_state = meta.get("state")
            meta = reconcile_state(meta)
            if meta.get("state") == "interrupted" and original_state != "interrupted":
                write_meta(project, entry.name, meta)
            if state_filter is None or meta.get("state") == state_filter:
                jobs.append(meta)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return jobs


def count_active(project: Path) -> int:
    return sum(
        1
        for meta in list_jobs(project)
        if meta.get("state") in ("running", "queued")
    )


def elapsed_seconds(meta: dict) -> float | None:
    started = meta.get("started_at")
    finished = meta.get("finished_at")
    if started is None:
        return None
    start_ts = datetime.fromisoformat(started).timestamp()
    end_ts = (
        datetime.fromisoformat(finished).timestamp()
        if finished
        else time.time()
    )
    return end_ts - start_ts


def latest_step_summary(project: Path, job_id: str) -> str | None:
    """Return the latest assistant/result summary from events.ndjson."""
    path = events_path(project, job_id)
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    summary = None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Real agy result envelope uses {"event": "result", "result": {...}}
        if event.get("event") == "result" and "result" in event:
            result = event["result"]
            return result.get("response", "")[:200] or result.get("status")
        if event.get("type") == "result":
            return event.get("response", "")[:200] or event.get("status")
        if event.get("role") == "assistant" or "content" in event:
            text = event.get("content") or event.get("text") or ""
            if text:
                summary = text[:200]
                break
    return summary
