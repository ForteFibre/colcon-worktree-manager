"""cwm doctor - cross-cutting health check across the base and all worktrees."""

from __future__ import annotations

import json

import click

from cwm.cli.base_cmd import _scan_stale_build_dirs
from cwm.cli.main import cli
from cwm.cli.status_cmd import _collect_base, _collect_worktrees, _print_base_status, _repos_label
from cwm.core.config import Config
from cwm.errors import CWMError
from cwm.util.filesystem import find_project_root


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (for scripting/agents).")
def doctor(as_json: bool) -> None:
    """Diagnose the overall health of the base workspace and all worktrees.

    Aggregates ``cwm ws status`` state with a count of stale base build
    directories. Deep stale-CMakeCache diagnosis and repair live in
    ``cwm base doctor`` (run that with --fix); this command only points there.
    """
    try:
        root = find_project_root()
        config = Config.load(root)
        from cwm.core.worktree_state import WorktreeStateManager
        manager = WorktreeStateManager(config)

        base_info = _collect_base(config)
        stale = _scan_stale_build_dirs(config.project_root / "build")
        base_info["stale_build_dirs"] = len(stale)
        worktrees_info = _collect_worktrees(config, manager)

        if as_json:
            click.echo(json.dumps({"base": base_info, "worktrees": worktrees_info}, indent=2))
            return

        _print_human(base_info, worktrees_info)

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


def _print_human(base: dict, worktrees: list[dict]) -> None:
    _print_base_status(base)

    stale = base["stale_build_dirs"]
    if stale:
        click.echo(
            "  " + click.style(f"{stale} stale build dir(s)", fg="yellow")
            + "  (run: cwm base doctor --fix)"
        )

    click.echo()
    if not worktrees:
        click.echo("No worktrees. Create one with: cwm worktree add <branch>")
        return

    click.echo("Worktrees:")
    for worktree in worktrees:
        if not worktree["exists"]:
            status_str = click.style("missing", fg="red") + " (meta exists, ws gone)"
        elif worktree["built"]:
            status_str = click.style("built", fg="green")
        else:
            status_str = click.style("not built", fg="yellow")

        dirty_str = click.style(" dirty", fg="red") if worktree["dirty"] else ""
        ahead_str = f"  +{worktree['ahead']} commit(s)" if worktree["ahead"] else ""
        missing = [r["repo"] for r in worktree.get("repos") or [] if worktree["exists"] and not r["exists"]]
        missing_str = (
            "  " + click.style(f"missing checkout(s): {', '.join(missing)}", fg="red") if missing else ""
        )

        click.echo(
            f"  {worktree['branch']}  {status_str}{dirty_str}{ahead_str}{_repos_label(worktree)}{missing_str}"
        )
