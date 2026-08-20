"""FIFO run-slot queue shared by all supervisors of one project.

`run`/`feedback` never reject a job for concurrency reasons; every job gets a
detached supervisor immediately and the supervisor blocks in `acquire_slot`
until fewer than `max_concurrent` jobs are `running`.  Slot accounting happens
under an flock so two supervisors cannot claim the same slot.
"""

from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from .jobs import list_jobs, project_bridge_dir, read_meta, write_meta


class QueueTimeout(RuntimeError):
    """Raised when a job waited for a run slot longer than allowed."""


def lock_path(project: Path) -> Path:
    path = project_bridge_dir(project) / "queue.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def queue_lock(project: Path) -> Iterator[None]:
    """Serialize slot accounting across supervisor processes."""
    with lock_path(project).open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _fifo_key(meta: dict) -> tuple[str, str]:
    """Sort key deciding who starts first; falls back to created_at for old metas."""
    return (meta.get("queued_at") or meta.get("created_at") or "", meta.get("id") or "")


def count_running(project: Path) -> int:
    return sum(1 for meta in list_jobs(project) if meta.get("state") == "running")


def queued_ahead(project: Path, job_id: str) -> int:
    """How many queued jobs are entitled to a slot before ``job_id``."""
    queued = [meta for meta in list_jobs(project) if meta.get("state") == "queued"]
    mine = next((meta for meta in queued if meta.get("id") == job_id), None)
    if mine is None:
        return 0
    my_key = _fifo_key(mine)
    return sum(1 for meta in queued if meta.get("id") != job_id and _fifo_key(meta) < my_key)


def queue_position(project: Path, job_id: str) -> int | None:
    """1-based FIFO position of a queued job (1 = next to start); None if not queued."""
    try:
        meta = read_meta(project, job_id)
    except FileNotFoundError:
        return None
    if meta.get("state") != "queued":
        return None
    return queued_ahead(project, job_id) + 1


def queue_forecast(project: Path, job_id: str, max_concurrent: int) -> int | None:
    """Position a freshly-enqueued job will actually wait at, or None if it starts now.

    `run`/`feedback` return before their supervisor has had a chance to claim a
    slot, so `queue_position` alone would report a phantom `1` for every job.
    This evaluates the same predicate `acquire_slot` will: no wait means None.
    """
    position = queue_position(project, job_id)
    if position is None:
        return None
    free = max_concurrent - count_running(project)
    return None if position <= free else position


def acquire_slot(
    project: Path,
    job_id: str,
    max_concurrent: int,
    poll_interval: float = 1.0,
    timeout: float | None = None,
    should_abort: Callable[[], bool] | None = None,
    on_wait: Callable[[int, int], None] | None = None,
) -> dict | None:
    """Block until a run slot frees up, then atomically mark the job ``running``.

    FIFO by ``queued_at``: a job claims a slot only when fewer than
    ``max_concurrent`` jobs are running *and* no earlier-queued job is still
    entitled to one of the free slots.

    Returns the updated meta, ``None`` if ``should_abort`` fired first, and
    raises :class:`QueueTimeout` if ``timeout`` seconds elapse while waiting.
    """
    deadline = time.time() + timeout if timeout is not None else None
    announced = False

    while True:
        if should_abort is not None and should_abort():
            return None

        with queue_lock(project):
            running = count_running(project)
            free = max_concurrent - running
            if free > 0 and queued_ahead(project, job_id) < free:
                meta = read_meta(project, job_id)
                meta["state"] = "running"
                meta["started_at"] = datetime.now(timezone.utc).isoformat()
                write_meta(project, job_id, meta)
                return meta
            ahead = queued_ahead(project, job_id)

        if on_wait is not None and not announced:
            on_wait(running, ahead)
            announced = True

        if deadline is not None and time.time() >= deadline:
            raise QueueTimeout(
                f"waited for a run slot longer than {timeout:.0f}s "
                f"({running} running, {ahead} queued ahead)"
            )

        time.sleep(poll_interval)
