"""Shell completion callbacks for the cwm CLI."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable

import click
from click.shell_completion import CompletionItem


@lru_cache(maxsize=1)
def _load_config_and_manager():
    from cwm.core.config import Config
    from cwm.core.worktree_state import WorktreeStateManager
    from cwm.util.filesystem import find_project_root

    root = find_project_root()
    config = Config.load(root)
    return config, WorktreeStateManager(config)


def _match(items: Iterable[str], incomplete: str) -> list[CompletionItem]:
    return [CompletionItem(s) for s in items if s.startswith(incomplete)]


def suppress_completion(ctx: click.Context, param: click.Parameter, incomplete: str) -> list:
    """Return no completions, suppressing Click's fallback to filesystem completion.

    Used on variadic ``colcon_args`` so tab-completion does not surface the
    workspace's build/ directory (compopt -o default).
    """
    return []


def complete_worktree_branches(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete with existing worktree branch names."""
    try:
        _, manager = _load_config_and_manager()
        return _match((m.branch for m in manager.list_worktrees()), incomplete)
    except Exception:
        return []


def complete_git_branches(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete with git branch names from every repository in the default set."""
    try:
        from cwm.util.git import list_branches

        config, _ = _load_config_and_manager()
        cwds = list(config.repo_paths.values()) or [config.project_root]
        branches: set[str] = set()
        for cwd in cwds:
            branches.update(list_branches(cwd=cwd, include_remote=True))
        return _match(sorted(branches), incomplete)
    except Exception:
        return []


def complete_repo_list(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete repository paths under src/, supporting comma-separated lists.

    For ``--repos a,b,<TAB>`` the already-typed prefix is kept and only the
    last segment is completed.
    """
    try:
        from cwm.util.repos import discover_sub_repos

        config, _ = _load_config_and_manager()
        head, sep, last = incomplete.rpartition(",")
        prefix = head + sep
        chosen = set(head.split(",")) if head else set()
        repos = [r for r in discover_sub_repos(config.base_src_path) if r not in chosen]
        return [CompletionItem(prefix + r) for r in repos if r.startswith(last)]
    except Exception:
        return []


def complete_worktree_repos(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete repositories checked out in the worktree named by ctx.params['branch']."""
    try:
        _, manager = _load_config_and_manager()
        branch = ctx.params.get("branch")
        if not branch:
            return []
        return _match(manager.get_worktree_meta(branch).repo_names, incomplete)
    except Exception:
        return []


def complete_distros(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete with available ROS 2 distro paths under /opt/ros/."""
    try:
        from cwm.util.ros_env import list_available_distros

        return _match(list_available_distros(), incomplete)
    except Exception:
        return []


def complete_cd_targets(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete cwm cd first argument: 'base', branch names, and active repo names."""
    items = ["base"]
    try:
        config, manager = _load_config_and_manager()
        items.extend(m.branch for m in manager.list_worktrees())
        branch = os.environ.get("CWM_WORKTREE")
        if os.environ.get("CWM_WORKSPACE") and branch:
            try:
                meta = manager.get_worktree_meta(branch)
                items.extend(config.checkout_name(rel) for rel in meta.repos)
            except Exception:
                pass
    except Exception:
        pass
    return _match(items, incomplete)


def complete_cd_repos(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete cwm cd second argument with the repo names of the branch in ctx.params['target']."""
    try:
        config, manager = _load_config_and_manager()
        target = ctx.params.get("target") or ctx.params.get("branch")
        if not target:
            return []
        meta = manager.get_worktree_meta(target)
        return _match([config.checkout_name(rel) for rel in meta.repos], incomplete)
    except Exception:
        return []
