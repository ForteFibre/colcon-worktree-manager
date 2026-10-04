"""cwm repo {show, add, remove, switch} - manage the default repository set."""

from __future__ import annotations

import click

from cwm.cli.main import cli
from cwm.core.config import Config
from cwm.errors import CWMError, RepoNameCollisionError
from cwm.util import git
from cwm.util.filesystem import find_project_root
from cwm.util.repos import discover_sub_repos, validate_repo_path


@cli.group()
def repo() -> None:
    """Manage the default set of git repositories checked out into new worktrees."""


repo.command_order = ["show", "add", "remove", "switch"]


def _load_with_src() -> Config:
    root = find_project_root()
    config = Config.load(root)
    if not config.base_src_path.exists():
        raise CWMError(
            f"src/ directory not found at {config.base_src_path}\n"
            "Clone your repository into src/ first."
        )
    return config


@repo.command("show")
def show() -> None:
    """List the repositories in the default set."""
    try:
        root = find_project_root()
        config = Config.load(root)

        if not config.repos:
            click.echo("No repository selected.")
            click.echo("Run: cwm repo add <path>")
            return

        click.echo("Default repositories:")
        for rel, repo_path in config.repo_paths.items():
            click.echo(f"  {rel}")
            if repo_path.exists():
                try:
                    branch = git.get_current_branch(cwd=repo_path)
                    sha = git.get_head_sha(cwd=repo_path)
                    click.echo(f"    Branch: {branch}")
                    click.echo(f"    HEAD:   {sha[:12]}")
                except CWMError:
                    pass
            else:
                click.secho(f"    Warning: path does not exist: {repo_path}", fg="yellow")

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


@repo.command("add")
@click.argument("path")
def add(path: str) -> None:
    """Add PATH (relative to src/) to the default repository set."""
    try:
        config = _load_with_src()
        validate_repo_path(config.base_src_path, path)

        if path in config.repos:
            click.echo(f"Already in the default set: {path}")
            return
        name = Config.checkout_name(path)
        for existing in config.repos:
            if Config.checkout_name(existing) == name:
                raise RepoNameCollisionError(
                    f"'{path}' shares the basename '{name}' with '{existing}'; "
                    "both cannot be checked out into one worktree."
                )

        config.repos.append(path)
        config.save()
        click.echo(f"Added to the default set: {path}")
        click.echo(f"Default repositories: {', '.join(config.repos)}")

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


@repo.command("remove")
@click.argument("path")
def remove(path: str) -> None:
    """Remove PATH from the default repository set.

    Existing worktrees are not affected; use 'cwm worktree focus --remove' for that.
    """
    try:
        root = find_project_root()
        config = Config.load(root)

        if path not in config.repos:
            raise CWMError(
                f"'{path}' is not in the default set ({', '.join(config.repos) or 'empty'})."
            )
        config.repos.remove(path)
        config.save()
        click.echo(f"Removed from the default set: {path}")
        click.echo(f"Default repositories: {', '.join(config.repos) or '(none)'}")

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


@repo.command("switch")
@click.argument("path")
def switch(path: str) -> None:
    """Set the default repository set to exactly PATH (relative to src/).

    PATH should be a git repository under the src/ directory, e.g.
    'autoware.universe' or 'core/autoware_core'.
    """
    try:
        config = _load_with_src()
        validate_repo_path(config.base_src_path, path)

        old_repos = list(config.repos)
        config.repos = [path]
        config.save()

        if old_repos and old_repos != [path]:
            click.echo(f"Switched default repositories: {', '.join(old_repos)} → {path}")
        else:
            click.echo(f"Default repository set to: {path}")

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


def _complete_repos(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list:
    from click.shell_completion import CompletionItem
    try:
        root = find_project_root()
        config = Config.load(root)
        repos = discover_sub_repos(config.base_src_path)
        return [CompletionItem(r) for r in repos if r.startswith(incomplete)]
    except Exception:
        return []


def _complete_default_repos(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list:
    from click.shell_completion import CompletionItem
    try:
        config = Config.load(find_project_root())
        return [CompletionItem(r) for r in config.repos if r.startswith(incomplete)]
    except Exception:
        return []


add.params[0].shell_complete = _complete_repos  # type: ignore[attr-defined]
switch.params[0].shell_complete = _complete_repos  # type: ignore[attr-defined]
remove.params[0].shell_complete = _complete_default_repos  # type: ignore[attr-defined]
