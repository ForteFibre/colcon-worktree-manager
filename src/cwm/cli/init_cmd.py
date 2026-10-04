"""cwm init - initialise a CWM project."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cwm.cli.completion import complete_distros
from cwm.cli.main import cli
from cwm.core.config import CONFIG_DIR
from cwm.core.worktree_state import WorktreeStateManager
from cwm.util.ros_env import ROS_INSTALL_BASE, detect_system_underlay, list_available_distros


@cli.command()
@click.option(
    "--underlay",
    default=None,
    shell_complete=complete_distros,
    help="Path to an additional ROS 2 underlay (auto-detected if omitted).",
)
@click.option(
    "--repo",
    "repo_paths",
    multiple=True,
    metavar="PATH",
    help="Repository for the default set (relative to src/). Repeatable. "
         "Auto-detected if src/ holds a single repository.",
)
def init(underlay: str, repo_paths: tuple[str, ...]) -> None:
    """Initialise a CWM project in the current directory.

    The current directory is treated as the base colcon workspace.
    If src/ already contains repositories, they are adopted as-is.
    """
    project_root = Path.cwd().resolve()

    if (project_root / CONFIG_DIR).is_dir():
        raise click.ClickException(
            f"CWM project already initialised at {project_root}"
        )

    if underlay is None:
        available = list_available_distros()
        detected = detect_system_underlay(available)
        if detected is None:
            if available:
                hint = f"Found: {', '.join(available)}. Use --underlay to specify one."
            else:
                hint = f"No ROS 2 installation found under {ROS_INSTALL_BASE}. Use --underlay to specify the path."
            raise click.ClickException(f"Could not auto-detect ROS 2 underlay. {hint}")
        underlay = detected
        click.echo(f"Auto-detected ROS 2 underlay: {underlay}")

    underlay_path = Path(underlay)
    if not underlay_path.is_dir():
        raise click.ClickException(f"Underlay path does not exist: {underlay}")

    src_path = project_root / "src"
    has_existing_src = src_path.is_dir() and any(src_path.iterdir())

    # Determine the default repository set
    selected_repos: list[str] = []
    if repo_paths:
        from cwm.util.repos import validate_repo_path
        from cwm.errors import RepoNotFoundError
        for rel in repo_paths:
            try:
                validate_repo_path(src_path, rel)
            except RepoNotFoundError as exc:
                raise click.ClickException(str(exc)) from exc
            if rel not in selected_repos:
                selected_repos.append(rel)
        _check_unique_basenames(selected_repos)
    elif has_existing_src:
        from cwm.util.repos import discover_sub_repos
        found = discover_sub_repos(src_path)
        if len(found) == 1:
            selected_repos = [next(iter(found))]
        elif len(found) > 1:
            selected_repos = _prompt_repo_selection(found)
            _check_unique_basenames(selected_repos)

    config = WorktreeStateManager.init_project(
        project_root,
        underlay=underlay,
        repos=selected_repos,
    )

    if has_existing_src:
        click.echo(f"Adopted existing workspace at {project_root}")
    else:
        click.echo(f"Initialised CWM project at {project_root}")
    click.echo(f"  Underlay:      {config.underlay}")
    click.echo(f"  Worktrees dir: {config.worktrees_path}")
    if config.repos:
        click.echo(f"  Default repos: {', '.join(config.repos)}")
    click.echo()
    click.echo("Next steps:")
    if has_existing_src:
        if not config.repos:
            click.echo("  0. Select repositories: cwm repo add <path>")
        click.echo("  1. Create a worktree:   cwm worktree add <branch>")
        click.echo("  2. Activate:            source <(cwm activate <branch>)")
        click.echo("  3. Build:               cwm ws build")
    else:
        click.echo("  1. Clone your repository into src/")
        click.echo("  2. Select it:           cwm repo add <path>")
        click.echo("  3. Build the base:      colcon build --symlink-install")
        click.echo("  4. Create a worktree:   cwm worktree add <branch>")


def _check_unique_basenames(repos: list[str]) -> None:
    """Reject a default set whose repositories would collide under a worktree's src/."""
    seen: dict[str, str] = {}
    for rel in repos:
        name = Path(rel).name
        if name in seen:
            raise click.ClickException(
                f"Repositories '{seen[name]}' and '{rel}' share the basename '{name}' "
                "and cannot be checked out into the same worktree."
            )
        seen[name] = rel


def _prompt_repo_selection(found: dict) -> list[str]:
    """Interactively prompt the user to pick repositories from *found*.

    Returns the selected relative paths (possibly empty if the user skips).
    """
    available = sorted(found.keys())
    if not sys.stdin.isatty():
        click.echo("Multiple repositories found in src/. Use --repo (repeatable) to specify:", err=True)
        for rel in available:
            click.echo(f"  {rel}", err=True)
        raise click.ClickException(
            "Cannot auto-select repositories in non-interactive mode.\n"
            "Use: cwm init --repo <path> [--repo <path> ...]"
        )

    click.echo("Multiple repositories found in src/:")
    for i, rel in enumerate(available, 1):
        click.echo(f"  [{i}] {rel}")
    click.echo()
    raw = click.prompt(
        "Select repositories (numbers separated by commas/spaces, or press Enter to skip)",
        default="",
        show_default=False,
    )
    tokens = [t for t in raw.replace(",", " ").split() if t]
    selected: list[str] = []
    for token in tokens:
        try:
            idx = int(token) - 1
        except ValueError:
            raise click.ClickException(f"Invalid input: {token!r}")
        if not 0 <= idx < len(available):
            raise click.ClickException(f"Invalid selection: {token}")
        if available[idx] not in selected:
            selected.append(available[idx])
    return selected
