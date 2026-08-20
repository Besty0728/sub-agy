"""argparse CLI entry point and subcommand dispatch."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, agy_bin_path, load_config, parse_timeout
from .jobs import (
    count_active,
    elapsed_seconds,
    generate_job_id,
    job_dir,
    latest_step_summary,
    list_jobs,
    meta_path,
    new_meta,
    read_meta,
    reconcile_state,
    result_path,
    update_meta,
    write_meta,
)
from .plan import Plan, assemble_prompt, parse_plan, write_plan_files
from .quota import fetch_quota
from .schema import EXIT_CODES, RESULT_SCHEMA
from .supervise import supervise_round
from .watch import watch_jobs
from .worktree import (
    add_worktree,
    delete_branch,
    ensure_exclude,
    git_head,
    is_git_repo,
    remove_worktree,
)


def _fmt_json(data: object, pretty: bool) -> str:
    if pretty:
        return json.dumps(data, indent=2, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)


def _err(message: str, code: int = EXIT_CODES["generic_error"]) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def _json_error(message: str, code: int = EXIT_CODES["generic_error"]) -> None:
    print(_fmt_json({"error": message}, False), file=sys.stdout)
    sys.exit(code)


def _resolve_project(args) -> Path:
    return Path(args.cwd).resolve()


def _write_supervisor_stderr(project: Path, job_id: str) -> Path:
    path = job_dir(project, job_id) / "stderr.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def cmd_run(args) -> None:
    config = load_config()
    if args.model:
        config = Config(**{**config.__dict__, "default_model": args.model})
    if args.effort:
        config = Config(**{**config.__dict__, "default_effort": args.effort})
    if args.timeout:
        try:
            parse_timeout(args.timeout)
        except ValueError as exc:
            _json_error(str(exc), EXIT_CODES["usage"])
        config = Config(**{**config.__dict__, "default_timeout": args.timeout})
    if args.auto_approve:
        config = Config(**{**config.__dict__, "auto_approve": True})

    project = _resolve_project(args)

    if args.plan:
        plan_text = Path(args.plan).read_text(encoding="utf-8")
    elif args.text:
        plan_text = args.text
    else:
        _json_error("--plan or --text required", EXIT_CODES["usage"])

    plan = parse_plan(plan_text)

    use_worktree = not args.no_worktree
    worktree: Path | None = None
    branch: str | None = None
    base_sha: str | None = None

    if use_worktree:
        if is_git_repo(project):
            ensure_exclude(project, project)
            base_sha = git_head(project)
            if base_sha is None:
                _json_error("failed to resolve HEAD", EXIT_CODES["not_git"])
        else:
            if not args.no_worktree:
                print(
                    _fmt_json(
                        {"warning": "not a git repo; running in cwd without worktree"},
                        args.pretty,
                    ),
                    file=sys.stderr,
                )
            use_worktree = False

    if count_active(project) >= config.max_concurrent:
        _json_error("max concurrent jobs reached", EXIT_CODES["concurrency"])

    job_id = generate_job_id()
    jdir = job_dir(project, job_id)
    jdir.mkdir(parents=True, exist_ok=True)

    if use_worktree:
        try:
            worktree, branch = add_worktree(project, project, job_id, base_sha)
        except subprocess.CalledProcessError as exc:
            _json_error(f"git worktree add failed: {exc.stderr}", EXIT_CODES["generic_error"])

    worktree_or_cwd = worktree if worktree else project
    prompt = assemble_prompt(plan, str(worktree_or_cwd), round_number=1)

    schema_text = json.dumps(RESULT_SCHEMA, indent=2)
    write_plan_files(jdir, plan, prompt, schema_text)

    if args.no_schema:
        schema_path = jdir / "schema.json"
        if schema_path.exists():
            schema_path.unlink()

    meta = new_meta(
        job_id,
        project,
        worktree,
        branch,
        base_sha,
        config.default_model,
        config.default_effort,
        config.default_timeout,
        auto_approve=config.auto_approve,
    )
    write_meta(project, job_id, meta)

    stderr_log = _write_supervisor_stderr(project, job_id)
    stderr_fh = stderr_log.open("a", encoding="utf-8")

    supervisor_argv = [
        sys.executable,
        "-m",
        "sub_agy.cli",
        "_supervise",
        job_id,
        "--round",
        "1",
    ]
    if args.auto_approve:
        supervisor_argv.append("--auto-approve")

    proc = subprocess.Popen(
        supervisor_argv,
        stdout=subprocess.DEVNULL,
        stderr=stderr_fh,
        start_new_session=True,
        cwd=str(project),
    )
    stderr_fh.close()

    meta = update_meta(project, job_id, pid_supervisor=proc.pid, state="queued")

    output = {
        "job_id": job_id,
        "worktree": str(worktree) if worktree else None,
        "branch": branch,
        "events_log": str(jdir / "events.ndjson"),
    }

    if args.wait:
        sys.exit(watch_jobs(project, [job_id], interval=2.0, timeout=3600.0, pretty=args.pretty))

    print(_fmt_json(output, args.pretty))
    sys.exit(EXIT_CODES["success"])


def cmd_status(args) -> None:
    project = _resolve_project(args)
    if args.all:
        jobs = list_jobs(project, state_filter=args.state)
        data = [
            {
                "id": m["id"],
                "state": m["state"],
                "round": m.get("round"),
                "elapsed": elapsed_seconds(m),
                "summary": latest_step_summary(project, m["id"]),
            }
            for m in jobs
        ]
        print(_fmt_json(data, args.pretty))
        return

    if not args.id:
        _json_error("job id required", EXIT_CODES["usage"])
    try:
        meta = reconcile_state(read_meta(project, args.id))
    except FileNotFoundError:
        _json_error("job not found", EXIT_CODES["not_found"])
    if meta.get("state") == "interrupted":
        write_meta(project, args.id, meta)
    data = {
        "id": meta["id"],
        "state": meta["state"],
        "round": meta.get("round"),
        "elapsed": elapsed_seconds(meta),
        "agy_status": meta.get("agy_status"),
        "summary": latest_step_summary(project, args.id),
    }
    print(_fmt_json(data, args.pretty))


def cmd_result(args) -> None:
    project = _resolve_project(args)
    if not args.id:
        _json_error("job id required", EXIT_CODES["usage"])
    try:
        meta = reconcile_state(read_meta(project, args.id))
    except FileNotFoundError:
        _json_error("job not found", EXIT_CODES["not_found"])

    if meta.get("state") not in ("done", "error", "cancelled", "interrupted"):
        print(_fmt_json({"error": "job not finished", "state": meta["state"]}, args.pretty))
        sys.exit(EXIT_CODES["not_finished"])

    rpath = result_path(project, args.id)
    if rpath.exists():
        result = json.loads(rpath.read_text(encoding="utf-8"))
    else:
        result = {"error": "no result.json available", "state": meta["state"]}

    if args.events:
        result["events_path"] = str(job_dir(project, args.id) / "events.ndjson")

    print(_fmt_json(result, args.pretty))


def cmd_watch(args) -> None:
    project = _resolve_project(args)
    if not args.id:
        _json_error("one or more job ids required", EXIT_CODES["usage"])

    interval = args.interval
    if interval < 0.5 or interval > 60:
        _json_error("interval must be between 0.5 and 60 seconds", EXIT_CODES["usage"])

    timeout: float | None = None
    if args.timeout:
        try:
            timeout = parse_timeout(args.timeout)
        except ValueError as exc:
            _json_error(str(exc), EXIT_CODES["usage"])

    code = watch_jobs(
        project,
        list(args.id),
        interval=interval,
        timeout=timeout,
        pretty=args.pretty,
    )
    sys.exit(code)


def cmd_feedback(args) -> None:
    project = _resolve_project(args)
    if not args.id or not args.message:
        _json_error("job id and message required", EXIT_CODES["usage"])
    try:
        meta = reconcile_state(read_meta(project, args.id))
    except FileNotFoundError:
        _json_error("job not found", EXIT_CODES["not_found"])

    if meta.get("state") not in ("done", "error"):
        _json_error("job must be done or error to give feedback", EXIT_CODES["generic_error"])
    if not meta.get("conversation_id"):
        _json_error("job has no conversation_id", EXIT_CODES["generic_error"])

    round_number = meta.get("round", 1) + 1
    rpath = result_path(project, args.id)
    prev_summary = ""
    if rpath.exists():
        result = json.loads(rpath.read_text(encoding="utf-8"))
        prev_summary = result.get("summary", "")

    plan = parse_plan((job_dir(project, args.id) / "plan.md").read_text(encoding="utf-8"))
    worktree_or_cwd = (
        Path(meta["worktree"]) if meta.get("worktree") else Path(meta["project"])
    )
    prompt = assemble_prompt(
        plan,
        str(worktree_or_cwd),
        round_number=round_number,
        prev_summary=prev_summary,
        feedback_message=args.message,
    )
    prompt_path = job_dir(project, args.id) / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    meta = update_meta(project, args.id, state="queued", round=round_number)

    stderr_log = _write_supervisor_stderr(project, args.id)
    stderr_fh = stderr_log.open("a", encoding="utf-8")
    supervisor_argv = [
        sys.executable,
        "-m",
        "sub_agy.cli",
        "_supervise",
        args.id,
        "--round",
        str(round_number),
    ]
    proc = subprocess.Popen(
        supervisor_argv,
        stdout=subprocess.DEVNULL,
        stderr=stderr_fh,
        start_new_session=True,
        cwd=str(project),
    )
    stderr_fh.close()
    meta = update_meta(project, args.id, pid_supervisor=proc.pid)

    print(
        _fmt_json(
            {"job_id": args.id, "round": round_number, "state": "queued"},
            args.pretty,
        )
    )


def cmd_cancel(args) -> None:
    project = _resolve_project(args)
    if not args.id:
        _json_error("job id required", EXIT_CODES["usage"])
    try:
        meta = read_meta(project, args.id)
    except FileNotFoundError:
        _json_error("job not found", EXIT_CODES["not_found"])

    supervisor_pid = meta.get("pid_supervisor")
    agy_pid = meta.get("pid_agy")

    killed = False
    if supervisor_pid and _pid_alive(supervisor_pid):
        os.kill(supervisor_pid, signal.SIGTERM)
        killed = True
    elif agy_pid and _pid_alive(agy_pid):
        try:
            os.killpg(os.getpgid(agy_pid), signal.SIGTERM)
            killed = True
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(agy_pid, signal.SIGTERM)
                killed = True
            except (ProcessLookupError, PermissionError, OSError):
                pass

    if killed:
        meta["state"] = "cancelled"
        meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_meta(project, args.id, meta)

    print(_fmt_json({"job_id": args.id, "state": "cancelled"}, args.pretty))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def cmd_list(args) -> None:
    project = _resolve_project(args)
    jobs = list_jobs(project, state_filter=args.state)
    if args.pretty:
        print(f"{'id':<30} {'state':<12} {'round':>5} {'elapsed':>10}")
        for m in jobs:
            elapsed = elapsed_seconds(m)
            print(
                f"{m['id']:<30} {m['state']:<12} {m.get('round', 1):>5} "
                f"{elapsed if elapsed is not None else '-':>10.1f}"
            )
    else:
        print(_fmt_json([{k: m[k] for k in ("id", "state", "round")} | {"elapsed": elapsed_seconds(m)} for m in jobs], False))


def cmd_cleanup(args) -> None:
    project = _resolve_project(args)
    if not args.id:
        _json_error("job id required", EXIT_CODES["usage"])
    try:
        meta = reconcile_state(read_meta(project, args.id))
    except FileNotFoundError:
        _json_error("job not found", EXIT_CODES["not_found"])

    if meta.get("state") in ("running", "queued") and not args.force:
        _json_error("cannot cleanup running/queued job without --force", EXIT_CODES["generic_error"])

    worktree = meta.get("worktree")
    branch = meta.get("branch")

    if worktree:
        remove_worktree(project, Path(worktree), force=True)
    if args.delete_branch and branch:
        delete_branch(project, branch)

    if args.purge:
        import shutil

        shutil.rmtree(job_dir(project, args.id), ignore_errors=True)

    print(_fmt_json({"job_id": args.id, "purged": args.purge}, args.pretty))


def cmd_doctor(args) -> None:
    issues: list[str] = []
    config = load_config()
    agy_path = agy_bin_path(config)

    agy_version: str | None = None
    try:
        result = subprocess.run(
            [agy_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            issues.append("agy --version failed")
        else:
            agy_version = result.stdout.strip().split()[0]
            try:
                parts = agy_version.split(".")
                if len(parts) >= 3 and (int(parts[0]), int(parts[1]), int(parts[2])) < (1, 1, 8):
                    issues.append(f"agy version {agy_version} < 1.1.8")
            except ValueError:
                pass
    except FileNotFoundError:
        issues.append("agy not found on PATH")
    except subprocess.TimeoutExpired:
        issues.append("agy --version timed out")

    try:
        git_result = subprocess.run(
            ["git", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        git_version = git_result.stdout.strip() if git_result.returncode == 0 else None
    except FileNotFoundError:
        issues.append("git not found")
        git_version = None

    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 11):
        issues.append(f"Python {py_version} < 3.11")

    auth_dirs = [
        Path.home() / ".config" / "antigravity",
        Path.home() / ".gemini" / "antigravity-cli",
    ]
    auth_found = any(d.exists() for d in auth_dirs)

    report = {
        "ok": len(issues) == 0,
        "agy": {"path": agy_path, "version": agy_version, "found": agy_version is not None},
        "git_version": git_version,
        "python_version": py_version,
        "auth_dirs": {str(d): d.exists() for d in auth_dirs},
        "auth_found": auth_found,
        "issues": issues,
    }
    print(_fmt_json(report, args.pretty))
    if issues:
        sys.exit(EXIT_CODES["generic_error"])


def cmd_quota(args) -> None:
    config = load_config()
    try:
        data = fetch_quota(config)
    except FileNotFoundError as exc:
        print(_fmt_json({"ok": False, "error": str(exc)}, False), file=sys.stdout)
        sys.exit(EXIT_CODES["not_installed"])
    except (RuntimeError, ValueError) as exc:
        print(_fmt_json({"ok": False, "error": str(exc)}, False), file=sys.stdout)
        sys.exit(EXIT_CODES["generic_error"])

    if args.pretty:
        for group in data.get("groups", []):
            print(f"[{group['name']}]")
            header = f"{'window':<10} {'remaining%':>10} {'reset_time':<25} {'reset_in':<20}"
            print(header.rstrip())
            for bucket in group.get("buckets", []):
                reset_time = bucket.get("reset_time") or "-"
                reset_in = bucket.get("reset_in") or "-"
                line = (
                    f"{bucket['window']:<10} {bucket['remaining_pct']:>10.1f} "
                    f"{reset_time:<25} {reset_in:<20}"
                )
                print(line.rstrip())
            print()
        print("5h 窗口用于平滑全球容量；weekly 与你的订阅档位挂钩")
    else:
        print(_fmt_json(data, False))


def cmd_supervise(args) -> None:
    project = _resolve_project(args)
    supervise_round(args.id, args.round, project)


def build_parser() -> None:
    parser = argparse.ArgumentParser(prog="sub-agy", description="Antigravity async job bridge")
    parser.add_argument("--pretty", action="store_true", help="human-readable output")
    parser.add_argument("--cwd", default=os.getcwd(), help="project working directory")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="enqueue a new agy job")
    run_p.add_argument("--plan", help="path to plan markdown file")
    run_p.add_argument("--text", help="plan text")
    run_p.add_argument("--model", help="model slug")
    run_p.add_argument("--effort", choices=["low", "medium", "high"], help="effort level")
    run_p.add_argument("--timeout", help="Go duration e.g. 30m")
    run_p.add_argument("--auto-approve", action="store_true", help="skip permissions")
    run_p.add_argument("--no-worktree", action="store_true", help="run directly in cwd")
    run_p.add_argument("--no-schema", action="store_true", help=argparse.SUPPRESS)
    run_p.add_argument(
        "--wait", action="store_true", help="wait for the job to finish before exiting"
    )
    run_p.set_defaults(func=cmd_run)

    status_p = sub.add_parser("status", help="show job status")
    status_p.add_argument("id", nargs="?", help="job id")
    status_p.add_argument("--all", action="store_true", help="list all jobs")
    status_p.add_argument("--state", help="filter by state")
    status_p.add_argument(
        "--json", action="store_true", help="JSON output (default; kept for explicitness)"
    )
    status_p.set_defaults(func=cmd_status)

    result_p = sub.add_parser("result", help="show job result")
    result_p.add_argument("id", help="job id")
    result_p.add_argument("--events", action="store_true", help="include events log path")
    result_p.add_argument(
        "--json", action="store_true", help="JSON output (default; kept for explicitness)"
    )
    result_p.set_defaults(func=cmd_result)

    watch_p = sub.add_parser("watch", help="wait for one or more jobs to finish")
    watch_p.add_argument("id", nargs="+", help="job id(s)")
    watch_p.add_argument(
        "--interval", type=float, default=2.0, help="poll interval in seconds (0.5-60, default 2)"
    )
    watch_p.add_argument(
        "--timeout", default="60m", help="max wait time e.g. 90s/30m/1h (default 60m)"
    )
    watch_p.add_argument(
        "--json", action="store_true", help="JSON output (default; kept for explicitness)"
    )
    watch_p.set_defaults(func=cmd_watch)

    feedback_p = sub.add_parser("feedback", help="send feedback for another round")
    feedback_p.add_argument("id", help="job id")
    feedback_p.add_argument("message", help="feedback text")
    feedback_p.add_argument("--timeout", help=argparse.SUPPRESS)
    feedback_p.set_defaults(func=cmd_feedback)

    cancel_p = sub.add_parser("cancel", help="cancel a job")
    cancel_p.add_argument("id", help="job id")
    cancel_p.set_defaults(func=cmd_cancel)

    list_p = sub.add_parser("list", help="list jobs")
    list_p.add_argument("--state", help="filter by state")
    list_p.add_argument(
        "--json", action="store_true", help="JSON output (default; kept for explicitness)"
    )
    list_p.set_defaults(func=cmd_list)

    cleanup_p = sub.add_parser("cleanup", help="cleanup a job")
    cleanup_p.add_argument("id", help="job id")
    cleanup_p.add_argument("--purge", action="store_true", help="remove job logs")
    cleanup_p.add_argument("--delete-branch", action="store_true", help="delete git branch")
    cleanup_p.add_argument("--force", action="store_true", help="cleanup running job")
    cleanup_p.set_defaults(func=cmd_cleanup)

    doctor_p = sub.add_parser("doctor", help="check environment")
    doctor_p.set_defaults(func=cmd_doctor)

    quota_p = sub.add_parser("quota", help="query Antigravity usage quotas")
    quota_p.set_defaults(func=cmd_quota)

    sup_p = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    sup_p.add_argument("id", help="job id")
    sup_p.add_argument("--round", type=int, default=1)
    sup_p.add_argument("--auto-approve", action="store_true", help=argparse.SUPPRESS)
    sup_p.set_defaults(func=cmd_supervise)

    return parser


_SUBCOMMANDS = {
    "run",
    "status",
    "result",
    "watch",
    "feedback",
    "cancel",
    "list",
    "cleanup",
    "doctor",
    "quota",
    "_supervise",
}


def _normalize_argv(argv: list[str]) -> list[str]:
    """Move global --pretty/--cwd flags before the subcommand so both positions work."""
    global_flags: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--cwd" and i + 1 < len(argv):
            global_flags.extend([arg, argv[i + 1]])
            i += 2
        elif arg == "--pretty":
            global_flags.append(arg)
            i += 1
        else:
            rest.append(arg)
            i += 1
    return global_flags + rest


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(argv if argv is not None else sys.argv[1:]))
    try:
        args.func(args)
    except SystemExit as exc:
        raise
    except Exception as exc:
        _json_error(str(exc), EXIT_CODES["generic_error"])
    return EXIT_CODES["success"]


if __name__ == "__main__":
    raise SystemExit(main())
