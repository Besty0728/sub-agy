"""Git worktree helpers and diff stat generation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import EXIT_CODES


def is_git_repo(cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--git-dir"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def git_head(cwd: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def ensure_exclude(cwd: Path, project: Path) -> None:
    """Append .subagy/ to .git/info/exclude without touching .gitignore."""
    git_dir_raw = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--git-dir"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if git_dir_raw.returncode != 0:
        return
    git_dir = Path(git_dir_raw.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = cwd / git_dir
    info_dir = git_dir / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    exclude = info_dir / "exclude"
    marker = ".subagy/\n"
    if exclude.exists():
        content = exclude.read_text(encoding="utf-8")
        if marker in content:
            return
    with exclude.open("a", encoding="utf-8") as fh:
        fh.write(marker)


def add_worktree(
    cwd: Path, project: Path, job_id: str, base_sha: str
) -> tuple[Path, str]:
    """Create a git worktree for the job. Returns (worktree_path, branch)."""
    worktree_dir = project / ".subagy" / "worktrees" / job_id
    branch = f"agy/{job_id}"
    subprocess.run(
        ["git", "-C", str(cwd), "worktree", "add", str(worktree_dir), "-b", branch, base_sha],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return worktree_dir, branch


def remove_worktree(cwd: Path, worktree: Path, force: bool = True) -> None:
    args = ["git", "-C", str(cwd), "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree))
    subprocess.run(args, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def delete_branch(cwd: Path, branch: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), "branch", "-D", branch],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def diff_stat(cwd: Path, base_sha: str) -> tuple[str, list[str]]:
    """Return (diff --stat text, list of changed filenames)."""
    stat_result = subprocess.run(
        ["git", "-C", str(cwd), "diff", "--stat", f"{base_sha}..HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    stat_text = stat_result.stdout.strip()

    names_result = subprocess.run(
        ["git", "-C", str(cwd), "diff", "--name-only", f"{base_sha}..HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    names = [line.strip() for line in names_result.stdout.splitlines() if line.strip()]
    return stat_text, names
