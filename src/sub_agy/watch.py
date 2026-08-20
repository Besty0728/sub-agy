"""Watch-style polling and final job summarization shared by watch and run --wait."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .jobs import elapsed_seconds, events_path, read_meta, reconcile_state, result_path, write_meta
from .schema import EXIT_CODES, extract_tokens, fmt_elapsed, fmt_tokens

TERMINAL_STATES = {"done", "error", "cancelled", "interrupted"}


def _build_job_summary(project: Path, job_id: str, meta: dict) -> dict:
    """Build the canonical watch output object for a single job."""
    rpath = result_path(project, job_id)
    result: dict = {}
    if rpath.exists():
        try:
            result = json.loads(rpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            result = {}

    structured = result.get("structured_output") if isinstance(result.get("structured_output"), dict) else None
    tests_passed = result.get("tests_passed")
    if tests_passed is None and structured is not None:
        tests_passed = structured.get("tests_passed")

    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None

    return {
        "job_id": meta.get("id", job_id),
        "state": meta.get("state"),
        "round": result.get("round", meta.get("round")),
        "agy_status": result.get("agy_status", meta.get("agy_status")),
        "summary": result.get("summary", ""),
        "contract_ok": result.get("contract_ok", False) if result else False,
        "tests_passed": tests_passed,
        "elapsed_seconds": elapsed_seconds(meta),
        "tokens": extract_tokens(usage),
        "diff_stat": result.get("diff_stat", "") if result else "",
        "result_path": str(rpath),
        "events_path": str(events_path(project, job_id)),
        "worktree": meta.get("worktree"),
        "branch": meta.get("branch"),
    }


def _compute_exit_code(summaries: list[dict]) -> int:
    if all(s["state"] == "done" for s in summaries):
        return EXIT_CODES["success"]
    return EXIT_CODES["generic_error"]


def _print_summaries(summaries: list[dict], pretty: bool) -> None:
    if pretty:
        print(
            f"{'job_id':<30} {'state':<12} {'round':>5} "
            f"{'elapsed':>10} {'tokens':>10} {'contract_ok':>10} {'summary'}"
        )
        for s in summaries:
            elapsed_str = fmt_elapsed(s.get("elapsed_seconds"))
            tokens_val = s.get("tokens")
            total_tokens = tokens_val.get("total") if isinstance(tokens_val, dict) else None
            tokens_str = fmt_tokens(total_tokens)
            contract = "true" if s.get("contract_ok") else "false"
            summary = (s.get("summary") or "")[:60]
            print(
                f"{s['job_id']:<30} {s['state']:<12} {s.get('round', 1):>5} "
                f"{elapsed_str:>10} {tokens_str:>10} {contract:>10} {summary}"
            )
    else:
        print(json.dumps(summaries, indent=2, ensure_ascii=False))


def watch_jobs(
    project: Path,
    job_ids: list[str],
    interval: float,
    timeout: float | None,
    pretty: bool,
    strict: bool = False,
) -> int:
    """Poll until all jobs are terminal, then print summaries and return an exit code.

    Returns:
        0   if all jobs reached terminal state (when strict=True, all must reach ``done``).
        1   if strict=True and any job reached ``error``/``cancelled``/``interrupted``.
        3   immediately if any job id does not exist.
        124 if the timeout expires before all jobs finish (current states printed).
    """
    # Validate existence up front; do not start polling if any id is missing.
    missing: list[str] = []
    metas: dict[str, dict] = {}
    for job_id in job_ids:
        try:
            meta = read_meta(project, job_id)
        except FileNotFoundError:
            missing.append(job_id)
            continue
        original_state = meta.get("state")
        meta = reconcile_state(meta)
        if meta.get("state") == "interrupted" and original_state != "interrupted":
            write_meta(project, job_id, meta)
        metas[job_id] = meta

    if missing:
        print(f"job not found: {', '.join(missing)}", file=sys.stderr)
        return EXIT_CODES["not_found"]

    deadline = time.time() + timeout if timeout is not None else None

    while True:
        all_terminal = True
        for job_id in job_ids:
            # §18.1.k: Handle meta file disappearance
            try:
                meta = read_meta(project, job_id)
            except FileNotFoundError:
                # Meta file was deleted; mark as missing (terminal state)
                meta = {"id": job_id, "state": "missing"}
                metas[job_id] = meta
                continue

            original_state = meta.get("state")
            meta = reconcile_state(meta)
            if meta.get("state") == "interrupted" and original_state != "interrupted":
                write_meta(project, job_id, meta)
            metas[job_id] = meta
            if meta.get("state") not in TERMINAL_STATES:
                all_terminal = False

        if all_terminal:
            break

        if deadline is not None and time.time() >= deadline:
            summaries = [_build_job_summary(project, jid, metas[jid]) for jid in job_ids]
            _print_summaries(summaries, pretty)
            return EXIT_CODES["timeout"]

        time.sleep(interval)

    summaries = [_build_job_summary(project, jid, metas[jid]) for jid in job_ids]
    _print_summaries(summaries, pretty)
    if strict:
        return _compute_exit_code(summaries)
    return EXIT_CODES["success"]
