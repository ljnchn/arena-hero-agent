#!/usr/bin/env python3
"""Synchronize a clean local trunk branch with its remote before editing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# The working trunk of this repository.  `main` is a stale fork point that the
# project left behind; day-to-day work lands on the branch below.
TRUNK_BRANCH = "codex/mass-army"
TRUNK_REMOTE = "ljnchn"
TRUNK_REF = f"refs/remotes/{TRUNK_REMOTE}/{TRUNK_BRANCH}"


class SyncError(RuntimeError):
    pass


def _git(repo: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SyncError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip()


def synchronize(repo: Path) -> str:
    repo = repo.resolve()
    if _git(repo, "rev-parse", "--is-inside-work-tree") != "true":
        raise SyncError(f"not a Git work tree: {repo}")

    branch = _git(repo, "branch", "--show-current")
    if branch != TRUNK_BRANCH:
        raise SyncError(
            f"expected branch {TRUNK_BRANCH}, found {branch or 'detached HEAD'}"
        )

    if dirty := _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SyncError(f"working tree is not clean:\n{dirty}")

    if _git(repo, "remote", "get-url", TRUNK_REMOTE, check=False) == "":
        raise SyncError(f"remote {TRUNK_REMOTE} is missing")

    _git(
        repo,
        "fetch",
        "--prune",
        TRUNK_REMOTE,
        f"+refs/heads/{TRUNK_BRANCH}:{TRUNK_REF}",
    )
    local = _git(repo, "rev-parse", "HEAD")
    remote = _git(repo, "rev-parse", TRUNK_REF)
    merge_base = _git(repo, "merge-base", "HEAD", TRUNK_REF)

    if local == remote:
        result = "up-to-date"
    elif local == merge_base:
        _git(repo, "merge", "--ff-only", TRUNK_REF)
        result = "fast-forwarded"
    elif remote == merge_base:
        raise SyncError(
            f"local {TRUNK_BRANCH} is ahead of {TRUNK_REMOTE}/{TRUNK_BRANCH}; "
            "push or reset it before editing"
        )
    else:
        raise SyncError(
            f"local {TRUNK_BRANCH} has diverged from {TRUNK_REMOTE}/{TRUNK_BRANCH}"
        )

    final_local = _git(repo, "rev-parse", "HEAD")
    final_remote = _git(repo, "rev-parse", TRUNK_REF)
    final_status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if final_local != final_remote or final_status:
        raise SyncError("post-sync verification failed")

    print(f"source-sync status={result} commit={final_local}")
    return final_local


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    try:
        synchronize(repo)
    except SyncError as error:
        print(f"source-sync refused: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
