"""cwm worktree {add, remove, list, prune} - manage overlay worktrees."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, NoReturn

import click

from cwm.cli.completion import (
    complete_git_branches,
    complete_repo_list,
    complete_worktree_branches,
    complete_worktree_repos,
)
from cwm.cli.main import worktree
from cwm.core.config import Config
from cwm.core.worktree_state import WorktreeMeta, WorktreeStateManager
from cwm.errors import CWMError
from cwm.util import git as gitutil
from cwm.util.filesystem import find_project_root
from cwm.util.repos import discover_sub_repos


def _load() -> tuple[Config, WorktreeStateManager]:
    root = find_project_root()
    config = Config.load(root)
    return config, WorktreeStateManager(config)


def _json_fail(msg: str) -> NoReturn:
    click.echo(json.dumps({"ok": False, "error": msg}))
    raise SystemExit(1)


def _json_ok(payload: dict[str, Any]) -> None:
    click.echo(json.dumps({"ok": True, **payload}))


def _split_repos(value: str | None) -> list[str] | None:
    """Parse a comma-separated --repos value; None/empty means 'use the default set'."""
    if not value:
        return None
    return [r.strip() for r in value.split(",") if r.strip()] or None


def _repo_payload(config: Config, branch: str, meta: WorktreeMeta) -> list[dict[str, Any]]:
    """Per-repo JSON entries for a worktree."""
    return [
        {
            "repo": rel,
            "src_path": str(config.worktree_checkout_path(branch, rel)),
            "base_sha": state.base_sha,
            "base_branch": state.base_branch,
        }
        for rel, state in meta.repos.items()
    ]


@worktree.command()
@click.argument("branch", shell_complete=complete_git_branches)
@click.option(
    "--repos",
    "repos_opt",
    default=None,
    metavar="PATH[,PATH...]",
    shell_complete=complete_repo_list,
    help="Comma-separated repositories (relative to src/) to check out. "
         "Defaults to the project's default set ('cwm repo show').",
)
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def add(branch: str, repos_opt: str | None, as_json: bool) -> None:
    """Create a new overlay worktree for BRANCH.

    Each selected repository is checked out at
    worktrees/<branch>_ws/src/<repo-basename> on BRANCH: an existing local
    branch is reused, else origin/BRANCH is tracked if it exists (after a
    best-effort fetch), else BRANCH is created from the base checkout's HEAD.
    """
    try:
        config, manager = _load()
        ws_path = manager.create_worktree(branch, _split_repos(repos_opt))
        meta = manager.get_worktree_meta(branch)

        if as_json:
            _json_ok({
                "branch": branch,
                "ws_path": str(ws_path),
                "ros_domain_id": meta.ros_domain_id,
                "repos": _repo_payload(config, branch, meta),
            })
        else:
            click.echo(f"Created worktree workspace: {ws_path}")
            for rel in meta.repos:
                click.echo(f"  Repo:    {rel}  ->  {config.worktree_checkout_path(branch, rel)}")
            click.echo(f"  Build:   {ws_path / 'build'}")
            click.echo(f"  Install: {ws_path / 'install'}")
            click.echo(f"  ROS_DOMAIN_ID: {meta.ros_domain_id}")
            click.echo()
            click.echo(f"Activate with: source <(cwm activate {branch})")
    except CWMError as exc:
        if as_json:
            _json_fail(str(exc))
        raise click.ClickException(str(exc)) from exc


@worktree.command("focus")
@click.argument("branch", shell_complete=complete_worktree_branches)
@click.option(
    "--add", "to_add", multiple=True, metavar="REPO",
    shell_complete=complete_repo_list,
    help="Check out REPO (relative to src/) in the worktree. Repeatable.",
)
@click.option(
    "--remove", "--rm", "to_remove", multiple=True, metavar="REPO",
    shell_complete=complete_worktree_repos,
    help="Remove REPO's checkout from the worktree. Repeatable.",
)
@click.option("--list", "list_only", is_flag=True, help="List the repositories in the worktree.")
@click.option("--force", is_flag=True, help="With --remove: discard uncommitted changes.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def focus(
    branch: str,
    to_add: tuple[str, ...],
    to_remove: tuple[str, ...],
    list_only: bool,
    force: bool,
    as_json: bool,
) -> None:
    """Add or remove repositories in an existing worktree.

    \b
        cwm worktree focus <branch> --add core/autoware_core
        cwm worktree focus <branch> --remove autoware_core
        cwm worktree focus <branch> --list

    Added repositories use the same branch resolution as 'cwm worktree add'.
    Removing the last repository is refused; use 'cwm worktree remove'.
    """
    try:
        config, manager = _load()
        added: list[str] = []
        removed: list[str] = []
        if to_add:
            added = manager.add_repos(branch, list(to_add))
        if to_remove:
            removed = manager.remove_repos(branch, list(to_remove), force=force)
        meta = manager.get_worktree_meta(branch)

        if as_json:
            _json_ok({
                "branch": branch,
                "added": added,
                "removed": removed,
                "repos": _repo_payload(config, branch, meta),
            })
            return

        for rel in added:
            click.echo(f"Added:   {rel}  ->  {config.worktree_checkout_path(branch, rel)}")
        for rel in removed:
            click.echo(f"Removed: {rel}")
        if list_only or not (added or removed):
            click.echo(f"Repositories in '{branch}':")
            for rel in meta.repos:
                click.echo(f"  {rel}  ->  {config.worktree_checkout_path(branch, rel)}")
    except CWMError as exc:
        if as_json:
            _json_fail(str(exc))
        raise click.ClickException(str(exc)) from exc


@worktree.command("remove")
@click.argument("branch", shell_complete=complete_worktree_branches)
@click.option("--force", is_flag=True, help="Force removal even with uncommitted changes.")
@click.option("--delete-branch", is_flag=True, help="Also delete the git branch (in every repo) after removing the worktree.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def remove(branch: str, force: bool, delete_branch: bool, as_json: bool) -> None:
    """Remove the overlay worktree for BRANCH (every repository checkout in it)."""
    try:
        config, manager = _load()
        if not force and not as_json:
            ws_path = config.worktree_ws_path(branch)
            click.echo("This will permanently remove:")
            click.echo(f"  Branch:    {branch}")
            click.echo(f"  Workspace: {ws_path}")
            try:
                repos = manager.get_worktree_meta(branch).repo_names
            except CWMError:
                repos = []
            if repos:
                click.echo(f"  Repos:     {', '.join(repos)}")
            if delete_branch:
                click.echo("  (git branch will also be deleted)")
            click.confirm("Continue?", abort=True)
        manager.remove_worktree(branch, force=force, delete_branch=delete_branch)

        if as_json:
            _json_ok({"branch": branch})
        else:
            click.echo(f"Removed worktree: {branch}")
    except CWMError as exc:
        if as_json:
            _json_fail(str(exc))
        raise click.ClickException(str(exc)) from exc


@worktree.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def list_worktrees_cmd(as_json: bool) -> None:
    """List all managed worktrees."""
    try:
        config, manager = _load()
        metas = manager.list_worktrees()

        if as_json:
            items = []
            for meta in metas:
                ws_path = config.worktree_ws_path(meta.branch)
                items.append({
                    "branch": meta.branch,
                    "ws_path": str(ws_path),
                    "exists": ws_path.exists(),
                    "created_at": meta.created_at,
                    "ros_domain_id": meta.ros_domain_id,
                    "repos": _repo_payload(config, meta.branch, meta),
                })
            _json_ok({"worktrees": items})
            return

        if not metas:
            click.echo("No worktrees. Create one with: cwm worktree add <branch>")
            return
        for meta in metas:
            ws_path = config.worktree_ws_path(meta.branch)
            status = "exists" if ws_path.exists() else click.style("missing", fg="red")
            repo_str = f"  [{', '.join(meta.repo_names)}]" if meta.repos else ""
            domain_str = f"  domain {meta.ros_domain_id}" if meta.ros_domain_id is not None else ""
            click.echo(f"  {meta.branch}  ({status}){repo_str}{domain_str}  created {meta.created_at}")
    except CWMError as exc:
        if as_json:
            _json_fail(str(exc))
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# Git hook helpers
# ---------------------------------------------------------------------------


def _parse_git_worktree_add(
    args: list[str],
) -> tuple[Path, str, list[str]] | None:
    """Parse ``git worktree add`` arguments and extract (path, branch, ignored_flags).

    Returns None when the arguments cannot be parsed or no positional path was
    supplied; callers should fall back to a user-facing error message in that
    case.

    Native git flags whose semantics CWM cannot fully reproduce (e.g.
    ``--detach``, ``--orphan``, ``--lock``) are returned in *ignored_flags* so
    the caller can emit a warning rather than silently dropping them.
    """
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--checkout", action="store_true")
    parser.add_argument("--no-checkout", action="store_true")
    parser.add_argument("--lock", action="store_true")
    parser.add_argument("--reason")
    parser.add_argument("--orphan", action="store_true")
    parser.add_argument("--track", action="store_true")
    parser.add_argument("--no-track", action="store_true")
    parser.add_argument("--guess-remote", action="store_true")
    parser.add_argument("-b", dest="new_branch")
    parser.add_argument("-B", dest="reset_new_branch")
    parser.add_argument("positional", nargs="*")
    try:
        parsed = parser.parse_args(args)
    except (argparse.ArgumentError, SystemExit):
        return None
    pos = parsed.positional or []
    if not pos:
        return None
    path = Path(pos[0])
    branch = parsed.new_branch or parsed.reset_new_branch
    if branch is None:
        branch = pos[1] if len(pos) >= 2 else path.name
    ignored: list[str] = []
    if parsed.force:
        ignored.append("--force")
    if parsed.detach:
        ignored.append("--detach")
    if parsed.no_checkout:
        ignored.append("--no-checkout")
    if parsed.lock:
        ignored.append("--lock")
    if parsed.orphan:
        ignored.append("--orphan")
    return path, branch, ignored


def _hook_msg(line: str, *, fg: str | None = None, bold: bool = False) -> None:
    click.secho(f"[CWM Agent Hook] {line}", fg=fg, bold=bold, err=True)


def _hook_unsupported(ctx: click.Context, subcmd: str) -> NoReturn:
    _hook_msg(
        f"'git worktree {subcmd}' is not supported under CWM. "
        f"Run 'cwm worktree --help' for available commands.",
        fg="yellow",
    )
    ctx.exit(1)


def _hook_retarget_unsupported(ctx: click.Context) -> NoReturn:
    _hook_msg(
        "'git -C/--git-dir/--work-tree ... worktree' targets a different "
        "repository and is not supported under CWM. cd into that repository "
        "and run 'cwm worktree' (or plain 'git worktree') there.",
        fg="yellow",
    )
    ctx.exit(1)


# git global options that consume the following token as their value.  When
# locating the real subcommand behind leading global options, these must be
# skipped in pairs so the value (e.g. the path after '-C') is not mistaken for
# the subcommand.
_GIT_GLOBAL_VALUE_OPTS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
)
# Global options that retarget git at a different repository or working tree.  A
# 'git worktree' invocation behind any of these cannot be safely re-homed onto
# the CWM-managed overlay, so it is refused rather than silently mishandled.
_GIT_RETARGET_OPTS = frozenset({"-C", "--git-dir", "--work-tree"})


def _strip_git_globals(args: list[str]) -> tuple[list[str], bool]:
    """Drop leading git global options from *args*.

    Returns the remaining args (starting at the first positional, i.e. the git
    subcommand) and whether any repository-retargeting option
    ('-C', '--git-dir', '--work-tree') appeared among them.
    """
    retargeting = False
    i = 0
    while i < len(args) and args[i].startswith("-"):
        name = args[i].split("=", 1)[0]
        if name in _GIT_RETARGET_OPTS:
            retargeting = True
        # '--opt=value' is self-contained; a bare value-taking option consumes
        # the next token too; every other flag consumes only itself.
        if "=" not in args[i] and args[i] in _GIT_GLOBAL_VALUE_OPTS:
            i += 2
        else:
            i += 1
    return args[i:], retargeting


def _repo_from_cwd(config: Config, manager: WorktreeStateManager) -> str | None:
    """Return the repository (relative to src/) whose checkout contains the cwd.

    Matches the base checkouts under src/ first, then the per-repo checkouts of
    existing CWM worktrees.  Returns None when the cwd is outside every
    repository (e.g. the project root), in which case the default set is used.
    """
    cwd = Path.cwd().resolve()
    src = config.base_src_path.resolve()
    if cwd.is_relative_to(src):
        for rel in discover_sub_repos(config.base_src_path):
            if cwd.is_relative_to(config.repo_path(rel).resolve()):
                return rel
    for meta in manager.list_worktrees():
        for rel in meta.repos:
            if cwd.is_relative_to(config.worktree_checkout_path(meta.branch, rel).resolve()):
                return rel
    return None


def _hook_add(ctx: click.Context, rest: list[str]) -> None:
    parsed = _parse_git_worktree_add(rest)
    if parsed is None:
        _hook_msg(
            "Could not parse 'git worktree add' arguments. "
            "Use 'cwm worktree add <branch>' instead.",
            fg="yellow",
        )
        ctx.exit(1)
    requested_path, branch, ignored_flags = parsed

    for flag in ignored_flags:
        _hook_msg(
            f"Note: '{flag}' is ignored - CWM always creates a managed overlay worktree.",
            fg="yellow",
        )

    # 'git worktree add' run inside a specific repository targets that
    # repository: create the CWM worktree with it, or add it to an existing
    # CWM worktree for the same branch (like 'cwm worktree focus --add').
    added_to_existing = False
    try:
        config, manager = _load()
        repo = _repo_from_cwd(config, manager)
        existing = None
        if config.worktree_meta_path(branch).exists():
            existing = manager.get_worktree_meta(branch)
        if existing is not None and repo is not None and repo not in existing.repos:
            manager.add_repos(branch, [repo])
            added_to_existing = True
            ws_path = config.worktree_ws_path(branch)
        else:
            ws_path = manager.create_worktree(branch, [repo] if repo else None)
    except CWMError as exc:
        _hook_msg(str(exc), fg="red")
        ctx.exit(1)

    try:
        link_path = manager.register_agent_symlink(branch, requested_path)
    except (CWMError, OSError) as exc:
        # Roll back only what this call created so a retry is not blocked and
        # a pre-existing worktree is left intact.
        try:
            if added_to_existing:
                manager.remove_repos(branch, [repo], force=True)
            else:
                manager.remove_worktree(branch, force=True)
        except CWMError:
            pass
        _hook_msg(
            f"Failed to create symlink at {requested_path}: {exc}",
            fg="red",
        )
        ctx.exit(1)

    meta = manager.get_worktree_meta(branch)

    if added_to_existing:
        _hook_msg(f"Intercepted 'git worktree add': added '{repo}' to existing worktree.", fg="green")
    else:
        _hook_msg("Intercepted 'git worktree add'.", fg="green")
    _hook_msg(f"  Real workspace:  {ws_path}", fg="cyan")
    _hook_msg(
        f"  Symlink at:      {link_path}  "
        "(use this path or the real one - they are equivalent)",
        fg="cyan",
    )
    for rel in meta.repos:
        _hook_msg(f"  Repo checkout:   {config.worktree_checkout_path(branch, rel)}", fg="cyan")
    _hook_msg(
        f"Next: run 'source <(cwm activate {branch})' "
        "to enter the CWM-managed environment.",
        fg="yellow",
        bold=True,
    )


def _hook_list(ctx: click.Context, rest: list[str]) -> None:
    porcelain = "--porcelain" in rest
    try:
        config, manager = _load()
        metas = manager.list_worktrees()
    except CWMError as exc:
        _hook_msg(str(exc), fg="red")
        ctx.exit(1)

    entries: list[tuple[Path, str, str]] = []  # (path, sha, branch)
    seen_paths: set[str] = set()

    # First, mirror what real git knows by asking every known base repository
    # for its worktree list.  This ensures we surface any worktrees created
    # outside of CWM (e.g. plain 'git worktree add' before adoption) so agents
    # get a complete picture.
    for rel in manager.known_repos(metas):
        base_repo = config.repo_path(rel)
        if not base_repo.exists():
            continue
        try:
            infos = gitutil.worktree_list(cwd=base_repo)
        except CWMError:
            infos = []
        for info in infos:
            key = str(info.path.resolve(strict=False))
            if key in seen_paths:
                continue
            seen_paths.add(key)
            entries.append((info.path, info.head, info.branch or ""))

    # Collapse each CWM-managed worktree (one checkout per repo) into a single
    # entry, replacing the workspace path with the agent-facing symlink when
    # one is registered.
    for meta in metas:
        ws_path = config.worktree_ws_path(meta.branch)
        checkouts = [config.worktree_checkout_path(meta.branch, rel) for rel in meta.repos]
        sha = next(iter(meta.repos.values())).base_sha if meta.repos else ""
        for checkout in checkouts:
            if checkout.exists():
                try:
                    sha = gitutil.get_head_sha(cwd=checkout)
                    break
                except CWMError:
                    pass
        display_path = (
            Path(meta.agent_symlinks[0]) if meta.agent_symlinks else ws_path
        )
        # Drop the per-repo entries git emitted for these checkouts so the
        # agent sees exactly one row per CWM worktree.
        checkout_keys = {str(c.resolve(strict=False)) for c in checkouts}
        entries = [
            (p, s, b)
            for (p, s, b) in entries
            if str(p.resolve(strict=False)) not in checkout_keys
        ]
        entries.append((display_path, sha, meta.branch))

    if porcelain:
        for path, sha, branch in entries:
            click.echo(f"worktree {path}")
            if sha:
                click.echo(f"HEAD {sha}")
            if branch:
                click.echo(f"branch refs/heads/{branch}")
            click.echo("")
    else:
        for path, sha, branch in entries:
            short_sha = sha[:7] if sha else ""
            branch_label = f"[{branch}]" if branch else "(detached HEAD)"
            click.echo(f"{path}\t{short_sha}\t{branch_label}")


def _hook_remove(ctx: click.Context, rest: list[str]) -> None:
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("positional", nargs="*")
    try:
        parsed = parser.parse_args(rest)
    except (argparse.ArgumentError, SystemExit):
        _hook_msg(
            "Could not parse 'git worktree remove' arguments. "
            "Use 'cwm worktree remove <branch>' instead.",
            fg="yellow",
        )
        ctx.exit(1)

    if not parsed.positional:
        _hook_msg("'git worktree remove' requires a <path> argument.", fg="yellow")
        ctx.exit(1)

    target = Path(os.path.abspath(parsed.positional[0]))
    config, manager = _load()

    target_resolved = target.resolve(strict=False)
    branch: str | None = None
    for meta in manager.list_worktrees():
        if str(target) in meta.agent_symlinks:
            branch = meta.branch
            break
        ws_resolved = config.worktree_ws_path(meta.branch).resolve(strict=False)
        if ws_resolved == target_resolved:
            branch = meta.branch
            break

    if branch is None:
        _hook_msg(
            f"No CWM worktree found for path: {target}. "
            "Use 'cwm worktree list' to inspect managed worktrees.",
            fg="yellow",
        )
        ctx.exit(1)

    try:
        manager.remove_worktree(branch, force=parsed.force)
    except CWMError as exc:
        _hook_msg(str(exc), fg="red")
        ctx.exit(1)

    _hook_msg(f"Removed worktree '{branch}' (was at {target})", fg="green")


def _hook_prune(ctx: click.Context, rest: list[str]) -> None:
    _config, manager = _load()
    try:
        pruned = manager.prune_stale()
    except CWMError as exc:
        _hook_msg(str(exc), fg="red")
        ctx.exit(1)
    _hook_msg(f"Pruned {len(pruned)} stale worktree(s).", fg="green")
    for branch in pruned:
        _hook_msg(f"  - {branch}", fg="cyan")


@worktree.command(
    "__git_hook",
    hidden=True,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.pass_context
def git_hook(ctx: click.Context) -> None:
    """Internal: handle 'git worktree ...' calls intercepted by the shell/PATH wrapper."""
    # The shell wrapper strips a bare leading 'worktree' token on its fast path,
    # but forwards global-option invocations (e.g. 'git -C <path> worktree ...')
    # unshifted so this hook can apply policy on them rather than let them slip
    # past to real git.  Skip any leading global options and refuse the ones
    # that retarget a different repository.
    args, retargeting = _strip_git_globals(list(ctx.args))
    if retargeting:
        _hook_retarget_unsupported(ctx)
    if args and args[0] == "worktree":
        args = args[1:]
    if not args:
        _hook_unsupported(ctx, "(no subcommand)")

    subcmd, rest = args[0], list(args[1:])
    if subcmd == "add":
        _hook_add(ctx, rest)
    elif subcmd == "list":
        _hook_list(ctx, rest)
    elif subcmd == "remove":
        _hook_remove(ctx, rest)
    elif subcmd == "prune":
        _hook_prune(ctx, rest)
    else:
        _hook_unsupported(ctx, subcmd)


@worktree.command("prune")
@click.option("--force", is_flag=True, help="Remove stale metadata without confirmation.")
def prune(force: bool) -> None:
    """Remove metadata for worktrees whose workspace directory no longer exists.

    Also runs 'git worktree prune' to clean up stale git worktree entries.
    """
    try:
        config, manager = _load()
        metas = manager.list_worktrees()
        stale_branches = [m.branch for m in metas if not config.worktree_ws_path(m.branch).exists()]

        if not stale_branches:
            click.echo("No stale worktrees found.")
            return

        click.echo("Stale worktrees (workspace directory missing):")
        for branch in stale_branches:
            click.echo(f"  {branch}")
        click.echo()

        if not force:
            click.confirm("Remove stale metadata?", abort=True)

        pruned = manager.prune_stale(stale_branches)
        for branch in pruned:
            click.echo(f"  Pruned: {branch}")
        click.echo(f"Pruned {len(pruned)} stale worktree(s).")
    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc
