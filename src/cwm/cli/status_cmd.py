"""cwm ws status - show overall state of base workspace and all worktrees."""

from __future__ import annotations

import json
import click

from cwm.cli.main import ws
from cwm.core.config import Config
from cwm.errors import CWMError, GitError
from cwm.util import git
from cwm.util.filesystem import find_project_root


@ws.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (for scripting/agents).")
def status(as_json: bool) -> None:
    """Show the state of the base workspace and all worktrees."""
    try:
        root = find_project_root()
        config = Config.load(root)
        from cwm.core.worktree_state import WorktreeStateManager
        manager = WorktreeStateManager(config)

        base_info = _collect_base(config)
        worktrees_info = _collect_worktrees(config, manager)

        if as_json:
            click.echo(json.dumps({"base": base_info, "worktrees": worktrees_info}, indent=2))
            return

        _print_human(base_info, worktrees_info)

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


def _is_dirty(path) -> bool:
    try:
        return git.is_dirty(cwd=path)
    except GitError:
        return False


def _collect_base(config: Config) -> dict:
    setup_bash = config.base_install_path / "setup.bash"
    built = setup_bash.exists()

    repos = []
    for rel, repo_path in config.repo_paths.items():
        exists = repo_path.exists()
        repos.append({
            "repo": rel,
            "exists": exists,
            "dirty": exists and _is_dirty(repo_path),
        })

    return {
        "built": built,
        "dirty": any(r["dirty"] for r in repos),
        "repos": repos,
    }


def _collect_worktrees(config: Config, manager) -> list[dict]:
    metas = manager.list_worktrees()
    result = []
    for meta in metas:
        ws_path = config.worktree_ws_path(meta.branch)
        exists = ws_path.exists()
        built = (config.worktree_install_path(meta.branch) / "local_setup.bash").exists()

        repos = []
        for rel, state in meta.repos.items():
            checkout = config.worktree_checkout_path(meta.branch, rel)
            present = exists and checkout.is_dir()
            dirty = present and _is_dirty(checkout)
            ahead = 0
            if present and state.base_branch:
                try:
                    ahead = git.commits_ahead(state.base_branch, cwd=checkout)
                except GitError:
                    pass
            repos.append({
                "repo": rel,
                "exists": present,
                "dirty": dirty,
                "ahead": ahead,
            })

        result.append({
            "branch": meta.branch,
            "repos": repos,
            "exists": exists,
            "built": built,
            "dirty": any(r["dirty"] for r in repos),
            "ahead": sum(r["ahead"] for r in repos),
            "created_at": meta.created_at,
        })
    return result


def _repos_label(entry: dict) -> str:
    """Render '  [a, b*]' for a status entry; '*' marks a dirty repository."""
    names = [r["repo"] + ("*" if r.get("dirty") else "") for r in entry.get("repos") or []]
    return f"  [{', '.join(names)}]" if names else ""


def _print_base_status(base: dict) -> None:
    """Print the one-line base workspace status (shared with ``cwm base status``)."""
    built_mark = click.style("built", fg="green") if base["built"] else click.style("not built", fg="yellow")
    dirty_mark = click.style(" dirty", fg="red") if base["dirty"] else ""
    click.echo(f"Base workspace  {built_mark}{dirty_mark}{_repos_label(base)}")


def _print_human(base: dict, worktrees: list[dict]) -> None:
    _print_base_status(base)

    if not worktrees:
        click.echo()
        click.echo("No worktrees. Create one with: cwm worktree add <branch>")
        return

    click.echo()
    click.echo("Worktrees:")
    for worktree in worktrees:
        if not worktree["exists"]:
            status_str = click.style("missing", fg="red")
        elif worktree["built"]:
            status_str = click.style("built", fg="green")
        else:
            status_str = click.style("not built", fg="yellow")

        dirty_str = click.style(" dirty", fg="red") if worktree["dirty"] else ""
        ahead_str = f"  +{worktree['ahead']} commit(s)" if worktree["ahead"] else ""
        click.echo(f"  {worktree['branch']}  {status_str}{dirty_str}{ahead_str}{_repos_label(worktree)}")
