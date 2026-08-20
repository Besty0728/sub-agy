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
) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    return {
        "id": job_id,
        "state": "queued",
        "created_at": created_at,
        "queued_at": created_at,
        "started_at": None,
        "finished_at": None,
        "agy_started_at": None,  # §18.1.d
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
        "conversation_id": None,
        "agy_status": None,
        "exit_code": None,
        "error": None,
        "attempts": 0,
        "recovered_from_transcript": False,
        "harvested_at": None,  # §18.2
    }


def write_meta(project: Path, job_id: str, meta: dict) -> None:
    """Atomically write meta using a tmp file + os.replace."""
    path = meta_path(project, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f"{path.name}.tmp"
    tmp_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


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


def transition(
    project: Path,
    job_id: str,
    expect_states: list[str],
    updates: dict,
) -> dict | None:
    """CAS-style state transition: reread meta under lock, ensure state ∈ expect_states,
    then merge updates and write back. Return updated meta on success, None on abort."""
    # Must be called from within queue_lock context by the caller
    try:
        meta = read_meta(project, job_id)
    except FileNotFoundError:
        return None
    if meta.get("state") not in expect_states:
        return None
    meta.update(updates)
    write_meta(project, job_id, meta)
    return meta


def reconcile_state(meta: dict) -> dict:
    """Lazily reconcile queued/running jobs whose supervisor has died."""
    state = meta.get("state")
    if state not in ("running", "queued"):
        return meta
    finished_at = meta.get("finished_at")
    supervisor_pid = meta.get("pid_supervisor")
    if finished_at is not None:
        return meta
    if state == "queued" and supervisor_pid is None:
        # run/feedback spawned the supervisor but has not recorded its pid yet;
        # check for ghost queued jobs waiting too long (§18.1.e)
        queued_at = meta.get("queued_at") or meta.get("created_at")
        if queued_at:
            queued_ts = datetime.fromisoformat(queued_at).timestamp()
            now_ts = time.time()
            if now_ts - queued_ts > 60:
                # Ghost queued job: supervisor never came online
                meta["state"] = "interrupted"
                meta["finished_at"] = datetime.now(timezone.utc).isoformat()
                meta["error"] = "supervisor did not start within 60s"
                return meta
        return meta
    if supervisor_pid is not None and _is_running(supervisor_pid):
        return meta
    # Supervisor dead and not finished -> check for existing result.json (§18.1.b)
    # If result exists for this round, don't overwrite to interrupted
    try:
        project = Path(meta.get("project", ""))
        if project.exists():
            rpath = result_path(project, meta.get("id", ""))
            if rpath.exists():
                result = json.loads(rpath.read_text(encoding="utf-8"))
                if result.get("round") == meta.get("round"):
                    # Result exists for this round: adopt its terminal state
                    # instead of clobbering to interrupted or staying running
                    rstate = result.get("state")
                    if rstate in ("done", "error"):
                        meta["state"] = rstate
                        if meta.get("finished_at") is None:
                            meta["finished_at"] = datetime.now(timezone.utc).isoformat()
                    return meta
    except (json.JSONDecodeError, OSError, ValueError):
        pass
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
    """Jobs occupying or waiting for a run slot. Informational only — `run` queues
    rather than rejects; the run-slot gate itself lives in `queue.acquire_slot`."""
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
    """Return the latest assistant/result summary from events.ndjson (tail 64KB).

    §18.1.p: Use tail read instead of loading entire file.
    """
    path = events_path(project, job_id)
    if not path.exists():
        return None
    try:
        file_size = path.stat().st_size
        # Read last 64KB (or entire file if smaller)
        read_size = min(file_size, 64 * 1024)
        with path.open("rb") as fh:
            if file_size > read_size:
                fh.seek(file_size - read_size)
            content = fh.read().decode("utf-8", errors="replace")
        lines = content.splitlines()
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
