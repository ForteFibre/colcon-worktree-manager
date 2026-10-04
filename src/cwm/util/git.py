"""Thin wrappers around git subprocess calls."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cwm.errors import GitError


def _friendly_hint(stderr: str) -> str | None:
    """Return a user-friendly hint for known git error patterns, or None."""
    if "already checked out at" in stderr:
        return (
            "This branch is already used by another worktree.\n"
            "  Remove the existing worktree first: cwm worktree rm <branch>\n"
            "  Or use a different branch name."
        )
    if "invalid reference" in stderr or "not a valid branch" in stderr:
        return "Branch not found. Check available branches with: git branch -a"
    if "already exists" in stderr:
        return "The worktree directory already exists. Remove it manually or use: cwm worktree prune"
    return None


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the CompletedProcess result.

    Sets CWM_GIT_HOOK_DEPTH=1 so the PATH wrapper at .cwm/bin/git transparently
    delegates to real git instead of re-entering 'cwm worktree __git_hook'.
    A *timeout* expiry is reported as GitError.
    """
    env = {**os.environ, "CWM_GIT_HOOK_DEPTH": "1", **(extra_env or {})}
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        hint = _friendly_hint(exc.stderr)
        msg = f"git {' '.join(args)} failed (rc={exc.returncode}): {exc.stderr.strip()}"
        if hint:
            msg += f"\n\nHint: {hint}"
        raise GitError(msg) from exc


# -- Repository queries -------------------------------------------------------


def is_git_repo(path: Path) -> bool:
    """Return True if *path* is the root of a git repository."""
    return (path / ".git").exists()


def get_toplevel(cwd: Path | None = None) -> Path:
    """Return the absolute path of the repository root."""
    result = _run(["rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(result.stdout.strip())


def get_current_branch(cwd: Path | None = None) -> str:
    """Return the name of the currently checked-out branch."""
    result = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return result.stdout.strip()


def get_merge_base(ref_a: str, ref_b: str, *, cwd: Path | None = None) -> str:
    """Return the merge-base commit hash between two refs."""
    result = _run(["merge-base", ref_a, ref_b], cwd=cwd)
    return result.stdout.strip()


def diff_name_only(
    base_ref: str,
    *,
    cwd: Path | None = None,
) -> list[str]:
    """Return the list of file paths changed between *base_ref* and HEAD."""
    result = _run(["diff", "--name-only", base_ref], cwd=cwd)
    return [line for line in result.stdout.splitlines() if line]


def get_head_sha(cwd: Path | None = None) -> str:
    """Return the full SHA of HEAD."""
    result = _run(["rev-parse", "HEAD"], cwd=cwd)
    return result.stdout.strip()


# -- Worktree management ------------------------------------------------------


@dataclass
class WorktreeInfo:
    """Parsed output of a single git worktree entry."""

    path: Path
    head: str
    branch: str | None  # None for detached HEAD


def list_branches(*, cwd: Path | None = None, include_remote: bool = True) -> list[str]:
    """Return a sorted, deduplicated list of local (and optionally remote) branch names."""
    result = _run(
        ["branch", "--format=%(refname:short)"],
        cwd=cwd,
        check=False,
    )
    seen: set[str] = {b.strip() for b in result.stdout.splitlines() if b.strip()}

    if include_remote:
        remote_result = _run(
            ["branch", "-r", "--format=%(refname:short)"],
            cwd=cwd,
            check=False,
        )
        for line in remote_result.stdout.splitlines():
            name = line.strip()
            if not name or "->" in name:
                continue
            short = name.split("/", 1)[1] if "/" in name else name
            seen.add(short)

    return sorted(seen)


def branch_exists(branch: str, *, cwd: Path | None = None) -> bool:
    """Check whether a local branch exists."""
    result = _run(
        ["rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=cwd,
        check=False,
    )
    return result.returncode == 0


def remote_branch_exists(branch: str, *, remote: str = "origin", cwd: Path | None = None) -> bool:
    """Check whether the remote-tracking branch ``<remote>/<branch>`` exists locally."""
    result = _run(
        ["rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}"],
        cwd=cwd,
        check=False,
    )
    return result.returncode == 0


def has_remote(remote: str = "origin", *, cwd: Path | None = None) -> bool:
    """Return True if *remote* is configured in the repository at *cwd*."""
    result = _run(["remote"], cwd=cwd, check=False)
    return remote in result.stdout.split()


# Upper bound for the opportunistic fetch done while resolving a branch.  Keeps
# 'cwm worktree add' usable offline or behind a slow/unreachable remote.
FETCH_TIMEOUT_SECONDS = 30.0


def try_fetch_branch(branch: str, *, remote: str = "origin", cwd: Path | None = None) -> bool:
    """Best-effort ``git fetch <remote> <branch>``; return True on success.

    Never raises: a missing remote, a branch that does not exist upstream, an
    offline machine or a timeout all just yield False.  Credential prompts are
    disabled so the call cannot block on a TTY.  With the default fetch refspec
    git also updates ``refs/remotes/<remote>/<branch>``.
    """
    if not has_remote(remote, cwd=cwd):
        return False
    try:
        result = _run(
            ["fetch", "--quiet", remote, branch],
            cwd=cwd,
            check=False,
            timeout=FETCH_TIMEOUT_SECONDS,
            extra_env={"GIT_TERMINAL_PROMPT": "0"},
        )
    except GitError:
        return False
    return result.returncode == 0


def pull(*, cwd: Path | None = None) -> None:
    """Run ``git pull`` in *cwd*."""
    _run(["pull"], cwd=cwd)


def is_dirty(cwd: Path | None = None) -> bool:
    """Return True if the working tree has uncommitted changes."""
    result = _run(["status", "--porcelain"], cwd=cwd, check=False)
    return bool(result.stdout.strip())


def commits_ahead(base_ref: str, *, cwd: Path | None = None) -> int:
    """Return number of commits HEAD is ahead of *base_ref*.

    Returns 0 on error (e.g. base_ref not found).
    """
    result = _run(
        ["rev-list", "--count", f"{base_ref}..HEAD"],
        cwd=cwd,
        check=False,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def worktree_prune(cwd: Path | None = None) -> None:
    """Run ``git worktree prune`` to remove stale worktree entries."""
    _run(["worktree", "prune"], cwd=cwd)


def worktree_add(
    path: Path,
    branch: str,
    *,
    create_branch: bool = True,
    start_point: str | None = None,
    track: bool = False,
    cwd: Path | None = None,
) -> None:
    """Run ``git worktree add``.

    If *create_branch* is True and the branch does not exist yet, it will be
    created with ``-b`` from *start_point* (default: HEAD).  *track* sets the
    new branch's upstream to *start_point* (used for ``origin/<branch>``).
    """
    exists = branch_exists(branch, cwd=cwd)
    args = ["worktree", "add"]
    if create_branch and not exists:
        if track:
            args.append("--track")
        args += ["-b", branch, str(path)]
        if start_point:
            args.append(start_point)
    else:
        args += [str(path), branch]
    _run(args, cwd=cwd)


def worktree_remove(path: Path, *, force: bool = False, cwd: Path | None = None) -> None:
    """Run ``git worktree remove``."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    _run(args, cwd=cwd)


def branch_delete(branch: str, *, force: bool = False, cwd: Path | None = None) -> None:
    """Delete a local branch."""
    args = ["branch", "-D" if force else "-d", branch]
    _run(args, cwd=cwd)


def worktree_list(cwd: Path | None = None) -> list[WorktreeInfo]:
    """Parse ``git worktree list --porcelain`` and return structured data."""
    result = _run(["worktree", "list", "--porcelain"], cwd=cwd)

    def _make_entry(fields: dict[str, str]) -> WorktreeInfo | None:
        if "worktree" not in fields:
            return None
        branch_raw = fields.get("branch")
        return WorktreeInfo(
            path=Path(fields["worktree"]),
            head=fields.get("HEAD", ""),
            branch=branch_raw.removeprefix("refs/heads/") if branch_raw else None,
        )

    entries: list[WorktreeInfo] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            entry = _make_entry(current)
            if entry:
                entries.append(entry)
            current = {}
            continue
        if line.startswith("worktree "):
            current["worktree"] = line.split(" ", 1)[1]
        elif line.startswith("HEAD "):
            current["HEAD"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1]

    # Handle last entry (no trailing blank line)
    entry = _make_entry(current)
    if entry:
        entries.append(entry)
    return entries
