"""cwm cd / cwm switch - worktree directory navigation."""

from __future__ import annotations

import os
import sys

import click

from cwm.cli.completion import complete_cd_repos, complete_cd_targets, complete_worktree_branches
from cwm.cli.main import cli
from cwm.core.config import Config
from cwm.core.worktree_state import WorktreeStateManager
from cwm.errors import CWMError
from cwm.util.filesystem import find_project_root

_CD_HINT = """\
cwm cd requires shell integration to change the current directory.

  Set up once (add to ~/.bashrc):
    eval "$(cwm shell-init)"

  Then use directly:
    cwm cd <branch>         # go to worktree workspace root
    cwm cd <branch> <repo>  # go to sub-repo inside worktree
    cwm cd <repo>           # go to sub-repo in active worktree
    cwm cd base             # go to project root
"""

_SWITCH_HINT = """\
cwm switch requires shell integration to activate and navigate in one step.

  Set up once (add to ~/.bashrc):
    eval "$(cwm shell-init)"

  Then use directly:
    cwm switch <branch>         # activate + go to workspace (or its only repo)
    cwm switch <branch> <repo>  # activate + go to specific sub-repo
"""


def _sole_checkout(config: Config, manager: WorktreeStateManager, branch: str) -> str | None:
    """Return the repo checkout path of *branch* if it has exactly one repo, else None."""
    try:
        meta = manager.get_worktree_meta(branch)
    except CWMError:
        return None
    if len(meta.repos) != 1:
        return None
    checkout = config.worktree_checkout_path(branch, meta.repo_names[0])
    return str(checkout) if checkout.exists() else None


def _resolve(args: tuple[str, ...], *, auto_subrepo: bool = False) -> str:
    """Return the resolved absolute path, or raise with an error message.

    With *auto_subrepo*, a worktree target resolves to its repository checkout
    when the worktree holds exactly one repository (multi-repo worktrees stay
    at the workspace root).
    """
    root = find_project_root()
    config = Config.load(root)
    manager = WorktreeStateManager(config)

    # No args → active workspace root (or sole repo checkout when auto_subrepo)
    if not args:
        ws = os.environ.get("CWM_WORKSPACE")
        if ws:
            branch = os.environ.get("CWM_WORKTREE")
            if auto_subrepo and branch:
                checkout = _sole_checkout(config, manager, branch)
                if checkout:
                    return checkout
            return ws
        raise CWMError(
            "No worktree specified. Usage: cwm cd <branch|repo|base>"
        )

    target = args[0]

    # base → project root
    if target == "base":
        return str(config.project_root)

    # Defer branch list until actually needed
    branches = [m.branch for m in manager.list_worktrees()]

    # Branch match → workspace root (or sole repo checkout) or named repo
    if target in branches:
        ws_path = config.worktree_ws_path(target)
        if len(args) == 1:
            if auto_subrepo:
                checkout = _sole_checkout(config, manager, target)
                if checkout:
                    return checkout
            return str(ws_path)
        repo_arg = args[1]
        meta = manager.get_worktree_meta(target)
        rel = meta.find_repo(repo_arg)
        if rel is None:
            raise CWMError(
                f"Repository '{repo_arg}' not found in worktree '{target}'. "
                f"Repos: {', '.join(meta.repo_names) or 'none'}"
            )
        return str(config.worktree_checkout_path(target, rel))

    # Active worktree → repo checkout within it
    ws = os.environ.get("CWM_WORKSPACE")
    if ws:
        branch = os.environ.get("CWM_WORKTREE", "")
        if branch:
            try:
                meta = manager.get_worktree_meta(branch)
                rel = meta.find_repo(target)
                if rel is not None:
                    return str(config.worktree_checkout_path(branch, rel))
            except CWMError:
                pass

    raise CWMError(
        f"No worktree or repository found for '{target}'. "
        "Use 'cwm worktree list' to see available branches."
    )


@cli.command(hidden=True, name="__cd-resolve")
@click.option("--auto-subrepo", is_flag=True)
@click.argument("args", nargs=-1)
def cd_resolve(args: tuple[str, ...], auto_subrepo: bool) -> None:
    """Internal: resolve a cd/switch target path. Used by the cwm shell function."""
    try:
        path = _resolve(args, auto_subrepo=auto_subrepo)
        click.echo(path)
    except CWMError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)


@cli.command("cd")
@click.argument("target", required=False, default=None, shell_complete=complete_cd_targets)
@click.argument("repo", required=False, default=None, shell_complete=complete_cd_repos)
def cd(target: str | None, repo: str | None) -> None:
    """Navigate to a worktree directory.

    Requires shell integration. Run 'eval "$(cwm shell-init)"' first.

    \\b
        cwm cd <branch>         # go to worktree workspace root
        cwm cd <branch> <repo>  # go to sub-repo inside worktree
        cwm cd <repo>           # go to sub-repo in active worktree
        cwm cd base             # go to project root
    """
    raise click.ClickException(_CD_HINT.rstrip())


@cli.command("switch")
@click.argument("branch", shell_complete=complete_worktree_branches)
@click.argument("repo", required=False, default=None, shell_complete=complete_cd_repos)
def switch(branch: str, repo: str | None) -> None:
    """Activate a worktree and navigate to it in one step.

    Requires shell integration. Run 'eval "$(cwm shell-init)"' first.

    \\b
        cwm switch <branch>         # activate + go to workspace (or its only repo)
        cwm switch <branch> <repo>  # activate + go to specific sub-repo
    """
    raise click.ClickException(_SWITCH_HINT.rstrip())
