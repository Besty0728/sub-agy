"""Tests for git worktree helpers."""

from __future__ import annotations

from pathlib import Path

from sub_agy.worktree import (
    add_worktree,
    delete_branch,
    ensure_exclude,
    git_head,
    is_git_repo,
    remove_worktree,
)


def test_is_git_repo(git_repo: Path) -> None:
    assert is_git_repo(git_repo) is True


def test_git_head(git_repo: Path) -> None:
    head = git_head(git_repo)
    assert head is not None
    assert len(head) == 40


def test_ensure_exclude(git_repo: Path) -> None:
    ensure_exclude(git_repo, git_repo)
    exclude = git_repo / ".git" / "info" / "exclude"
    assert exclude.exists()
    assert ".subagy/" in exclude.read_text(encoding="utf-8")


def test_add_remove_worktree(git_repo: Path) -> None:
    head = git_head(git_repo)
    worktree, branch = add_worktree(git_repo, git_repo, "j-test", head)
    assert worktree.exists()
    assert branch == "agy/j-test"
    (worktree / "hello.txt").write_text("hi\n", encoding="utf-8")
    remove_worktree(git_repo, worktree, force=True)
    assert not worktree.exists()


def test_delete_branch(git_repo: Path) -> None:
    head = git_head(git_repo)
    worktree, branch = add_worktree(git_repo, git_repo, "j-del", head)
    remove_worktree(git_repo, worktree, force=True)
    delete_branch(git_repo, branch)
    result = __import__("subprocess").run(
        ["git", "-C", str(git_repo), "branch", "--list", branch],
        capture_output=True,
        text=True,
    )
    assert branch not in result.stdout
