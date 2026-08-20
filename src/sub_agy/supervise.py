"""Supervisor subprocess that hosts one agy round."""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, agy_bin_path, load_config, parse_timeout
from .jobs import (
    events_path,
    job_dir,
    read_meta,
    result_path,
    stderr_path,
    update_meta,
    write_meta,
)
from .queue import QueueTimeout, acquire_slot
from .worktree import diff_stat


def _brain_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "brain"


def _write_event(project: Path, job_id: str, event: dict) -> None:
    path = events_path(project, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _write_round_separator(project: Path, job_id: str, round_number: int) -> None:
    _write_event(project, job_id, {"type": "round", "n": round_number})


def _find_transcript_response(
    start_time: float, meta: dict, project: Path
) -> str | None:
    """Fallback: find transcript.jsonl under brain dir with strict validation (§18.1.i).

    Only recover if exactly one brain directory's mtime falls in [started_at, now) and
    its UUID is not used by any other job in this project.
    """
    brain_dir = _brain_dir()
    if not brain_dir.exists():
        return None

    started_at = meta.get("agy_started_at")
    if not started_at:
        return None

    try:
        start_ts = datetime.fromisoformat(started_at).timestamp()
    except (ValueError, TypeError):
        return None

    now = time.time()

    # Find candidate brain directories with mtime in [started_at - 60s, now]
    # (allowing some tolerance for system clock skew, I/O delays, etc.)
    candidates: list[tuple[Path, str]] = []
    if brain_dir.exists():
        tolerance = 60  # Allow 60s before started_at for system clock skew
        for entry in brain_dir.iterdir():
            if not entry.is_dir():
                continue
            transcript = entry / ".system_generated" / "logs" / "transcript.jsonl"
            if not transcript.exists():
                continue
            mtime = transcript.stat().st_mtime
            # Within job start window with tolerance
            if mtime < start_ts - tolerance or mtime > now:
                continue
            candidates.append((transcript, entry.name))  # entry.name is uuid

    if len(candidates) != 1:
        # Zero or multiple candidates -> cannot uniquely associate
        return None

    best_path, uuid = candidates[0]

    # Verify uuid is not used by any other job in this project (§18.1.i)
    from .jobs import list_jobs
    for other_meta in list_jobs(project):
        if other_meta.get("id") == meta.get("id"):
            continue
        # Only check if other job has a conversation_id (not None)
        if other_meta.get("conversation_id") and other_meta.get("conversation_id") == uuid:
            # UUID used by another job -> cannot recover
            return None

    try:
        lines = best_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    last_text: str | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("role") == "assistant":
            text = _extract_text(record.get("content")) or record.get("text") or ""
            if text:
                last_text = text
    return last_text


def _extract_text(content: object) -> str:
    """Extract assistant text from a string or content-block list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_text = block.get("text")
                if isinstance(block_text, str):
                    parts.append(block_text)
        return "\n".join(parts)
    return ""


def _parse_result_event(events: list[dict]) -> dict | None:
    # SPEC-DEVIATION: SPEC §5 says look for type=="result".  The real agy
    # stream-json envelope uses {"event": "result", "result": {...}} instead,
    # so we support both shapes.
    for event in events:
        if event.get("type") == "result":
            return event
        if event.get("event") == "result" and "result" in event:
            return event["result"]
    return None


def _run_agy_once(
    meta: dict,
    prompt: str,
    schema_path: Path | None,
    worktree_or_cwd: Path,
    config: Config,
    round_number: int,
    drop_schema: bool,
    timeout: float,
) -> tuple[subprocess.Popen | None, list[dict], int | None]:
    """Spawn agy, read stdout events, and enforce a wall-clock timeout.

    Returns (process, events, exit_code).  exit_code is None on timeout.
    """
    # SPEC-DEVIATION: SPEC §5 lists `--cwd` in the agy argv, but the locally
    # installed agy (1.1.13) rejects `--cwd` ("flags provided but not defined").
    # We rely on subprocess.Popen(..., cwd=worktree_or_cwd) instead, which has
    # the same effect for a non-interactive agy process.
    argv = [
        agy_bin_path(config),
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--model",
        meta["model"],
        "--effort",
        meta["effort"],
        "--print-timeout",
        meta["timeout"],
    ]
    if not drop_schema and schema_path and schema_path.exists():
        argv.extend(["--json-schema", str(schema_path)])
    if round_number >= 2 and meta.get("conversation_id"):
        argv.extend(["--conversation", meta["conversation_id"]])
    argv.append("--dangerously-skip-permissions")

    project = Path(meta["project"])
    jdir = job_dir(project, meta["id"])
    stderr_file = stderr_path(project, meta["id"])
    jdir.mkdir(parents=True, exist_ok=True)

    stderr_fh = stderr_file.open("a", encoding="utf-8")
    stderr_fh.write(f"\n--- round {round_number} argv: {json.dumps(argv)} ---\n")
    stderr_fh.flush()

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=stderr_fh,
        text=True,
        bufsize=1,
        start_new_session=True,
        cwd=str(worktree_or_cwd),
    )

    # Immediately record agy process ownership (§18.1.d)
    update_meta(project, meta["id"], pid_agy=proc.pid, agy_started_at=datetime.now(timezone.utc).isoformat())

    events: list[dict] = []
    events_lock = threading.Lock()

    def _reader() -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    stderr_fh.write(f"[parse error] {line}\n")
                    stderr_fh.flush()
                    continue
                with events_lock:
                    events.append(event)
                _write_event(project, meta["id"], event)
        finally:
            stderr_fh.close()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        exit_code = None

    reader_thread.join(timeout=5)

    # Clear agy process ownership after exit (§18.1.d)
    update_meta(project, meta["id"], pid_agy=None)

    return proc, events, exit_code


def supervise_round(job_id: str, round_number: int, project: Path | None = None) -> None:
    """Internal entry point for _supervise command."""
    if project is None:
        project = Path.cwd()
    meta = read_meta(project, job_id)

    worktree_or_cwd = Path(meta["worktree"]) if meta.get("worktree") else Path(meta["project"])
    jdir = job_dir(project, job_id)
    prompt_path = jdir / "prompt.txt"
    prompt = prompt_path.read_text(encoding="utf-8")
    schema_path = jdir / "schema.json"
    if not schema_path.exists():
        schema_path = None
    config = load_config()
    # §18.1.q: Use dataclasses.replace for Config overrides
    config = dataclasses.replace(
        config,
        default_model=meta["model"],
        default_effort=meta["effort"],
        default_timeout=meta["timeout"],
    )

    cancelled = False

    def _handle_signal(signum: int, frame: object) -> None:
        nonlocal cancelled
        cancelled = True
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    proc: subprocess.Popen | None = None

    # Claim ownership before queueing so `status`/`list` can reconcile a
    # supervisor that dies while still waiting for a run slot.
    meta["pid_supervisor"] = os.getpid()
    meta["round"] = round_number
    write_meta(project, job_id, meta)

    try:
        slot_meta = acquire_slot(
            project,
            job_id,
            max_concurrent=config.max_concurrent,
            timeout=parse_timeout(config.queue_timeout),
            should_abort=lambda: cancelled,
            expect_round=round_number,  # §18.1.f: claim validation
            on_wait=lambda running, ahead: _write_event(
                project,
                job_id,
                {"type": "queued", "running": running, "queued_ahead": ahead},
            ),
        )
    except QueueTimeout as exc:
        meta = read_meta(project, job_id)
        meta["state"] = "error"
        meta["error"] = str(exc)
        meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_meta(project, job_id, meta)
        return

    if slot_meta is None:
        # Cancelled while queued, or claim validation failed (§18.1.f); exit without changing meta
        return

    meta = slot_meta
    meta["attempts"] = meta.get("attempts", 0) + 1
    write_meta(project, job_id, meta)

    _write_round_separator(project, job_id, round_number)

    timeout_seconds = parse_timeout(meta["timeout"])
    grace = int(os.environ.get("_SUB_AGY_GRACE_SECONDS", "60"))
    max_attempts = max(1, config.max_retries + 1)
    # §18.1.h: Total budget = timeout_seconds * max_attempts + grace
    wall_clock = timeout_seconds * max_attempts + grace
    start_time = time.time()

    final_event: dict | None = None
    exit_code: int | None = None
    drop_schema = False
    contract_note: str | None = None

    attempts = 0

    while attempts < max_attempts:
        # Check wall-clock budget at loop top (§18.1.h)
        remaining = wall_clock - (time.time() - start_time)
        if remaining <= 0:
            break
        if cancelled:
            break

        attempts += 1
        # §18.1.h: Single attempt timeout = min(timeout_seconds + grace, remaining)
        attempt_timeout = min(timeout_seconds + grace, remaining)
        proc, events, exit_code = _run_agy_once(
            meta,
            prompt,
            schema_path,
            worktree_or_cwd,
            config,
            round_number,
            drop_schema,
            timeout=attempt_timeout,
        )
        if proc is None:
            break
        meta = update_meta(project, job_id, attempts=attempts)

        if exit_code is None:
            # Timed out; loop will retry if attempts remain.
            continue

        if cancelled:
            break

        final_event = _parse_result_event(events)

        # Graceful degradation for round>=2 schema failures.
        if (
            round_number >= 2
            and not drop_schema
            and (
                final_event is None
                or final_event.get("status") in ("ERROR", "INVALID")
            )
            and exit_code != 0
        ):
            # Heuristic: parameter error caused by --json-schema combination
            drop_schema = True
            contract_note = "schema dropped on round>=2"
            continue

        break

    meta = read_meta(project, job_id)

    if cancelled:
        meta["state"] = "cancelled"
        meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        meta["exit_code"] = exit_code if exit_code is not None else -1
        write_meta(project, job_id, meta)
        return

    agy_status = None
    response_text = ""
    structured_output = None
    conversation_id = meta.get("conversation_id")
    duration_seconds = 0.0
    num_turns = 0
    usage: dict | None = None
    recovered = False

    if final_event is not None:
        agy_status = final_event.get("status")
        response_text = final_event.get("response", "")
        structured_output = final_event.get("structured_output")
        conversation_id = final_event.get("conversation_id") or conversation_id
        duration_seconds = final_event.get("duration_seconds", 0.0)
        num_turns = final_event.get("num_turns", 0)
        usage = final_event.get("usage")

    # stdout bug fallback with strict validation (§18.1.i)
    if (not final_event or not response_text) and not cancelled:
        # Reread meta to get latest agy_started_at set by _run_agy_once
        current_meta = read_meta(project, job_id)
        transcript_response = _find_transcript_response(start_time, current_meta, project)
        if transcript_response:
            response_text = transcript_response
            recovered = True
            agy_status = "RECOVERED"  # Mark as recovered (§18.1.i)
            if final_event is None:
                final_event = {"type": "result", "response": response_text}

    # Determine final state
    if final_event is None and not cancelled:
        # No result event and no transcript fallback
        meta["state"] = "error"
        meta["error"] = "no result event from agy and no transcript fallback"
    elif exit_code != 0 and not cancelled:
        meta["state"] = "error"
        if meta.get("error") is None:
            meta["error"] = f"agy exited with code {exit_code}"
    elif agy_status not in ("SUCCESS", None) and not cancelled:
        meta["state"] = "error"
        if meta.get("error") is None:
            meta["error"] = f"agy status {agy_status}"
    elif not cancelled:
        meta["state"] = "done"

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["exit_code"] = exit_code
    meta["agy_status"] = agy_status
    meta["conversation_id"] = conversation_id
    meta["recovered_from_transcript"] = recovered or meta.get("recovered_from_transcript", False)

    # §18.1.c: Write result.json first, then flip meta to terminal state
    # Build result for both done and error states
    summary = response_text[:500]
    contract_ok = True
    if structured_output:
        summary = structured_output.get("summary") or summary
    else:
        contract_ok = False

    if contract_note:
        contract_ok = False

    try:
        stat_text, names = diff_stat(worktree_or_cwd, meta.get("base_sha") or "HEAD")
    except Exception:
        stat_text = ""
        names = []

    if structured_output and isinstance(structured_output.get("files_changed"), list):
        names = structured_output["files_changed"]

    result = {
        "job_id": job_id,
        "state": meta["state"],
        "agy_status": agy_status or "UNKNOWN",
        "round": round_number,
        "summary": summary,
        "structured_output": structured_output,
        "contract_ok": contract_ok,
        "response_text": response_text,
        "files_changed_git": names,
        "diff_stat": stat_text,
        "usage": usage,
        "conversation_id": conversation_id,
        "duration_seconds": duration_seconds,
        "num_turns": num_turns,
        "recovered_from_transcript": recovered,
        "attempts": attempts,
        "worktree": meta.get("worktree"),
        "branch": meta.get("branch"),
        "base_sha": meta.get("base_sha"),
    }
    if contract_note:
        result["contract_note"] = contract_note

    # Write result.json before updating meta to terminal state (§18.1.c)
    if meta["state"] in ("done", "error"):
        result_file = result_path(project, job_id)
        result_file.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Finally, write meta with terminal state (§18.1.c)
    write_meta(project, job_id, meta)
