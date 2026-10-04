"""CWM CLI entry point and top-level command groups."""

from __future__ import annotations

import os
from pathlib import Path

import click

from cwm import __version__
from cwm.errors import CWMError, NotActivatedError
from cwm.util.colcon_runner import run_colcon


class HelpfulCommand(click.Command):
    """Command that shows full help on usage errors instead of 'Try --help'."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as e:
            click.echo(ctx.get_help(), err=True)
            click.echo(err=True)
            click.secho(f"Error: {e.format_message()}", fg="red", err=True)
            ctx.exit(2)


class HelpfulGroup(click.Group):
    """Group that shows full help on usage errors instead of 'Try --help'."""

    command_class = HelpfulCommand
    group_class = type  # Subgroups use the same class

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as e:
            click.echo(ctx.get_help(), err=True)
            click.echo(err=True)
            click.secho(f"Error: {e.format_message()}", fg="red", err=True)
            ctx.exit(2)

    def get_command(self, ctx: click.Context, name: str) -> click.BaseCommand | None:
        cmd = super().get_command(ctx, name)
        if cmd is not None:
            return cmd
        moved_commands = getattr(self, "moved_commands", {})
        if name in moved_commands:
            return _make_moved_command(name, moved_commands[name])
        if getattr(self, "enable_passthrough", False):
            return _make_colcon_passthrough(name)
        return None

    def list_commands(self, ctx: click.Context) -> list[str]:
        ordered = getattr(self, "command_order", ())
        commands = list(self.commands)
        if not ordered:
            return commands
        index = {name: i for i, name in enumerate(ordered)}
        return sorted(commands, key=lambda name: index.get(name, len(index)))

    def resolve_command(self, ctx: click.Context, args: list[str]):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as e:
            click.echo(ctx.get_help(), err=True)
            click.echo(err=True)
            click.secho(f"Error: {e.format_message()}", fg="red", err=True)
            ctx.exit(2)


def _make_colcon_passthrough(verb: str) -> click.Command:
    """Return a Click command that forwards ``cwm <verb> [args...]`` to ``colcon <verb> [args...]``.

    The workspace is taken from the active CWM environment variables set by
    ``cwm activate``.  Raises NotActivatedError when no workspace is active.
    """

    @click.command(
        name=verb,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
        help=f"Forward to ``colcon {verb}`` in the active worktree workspace.",
    )
    @click.argument("colcon_args", nargs=-1, type=click.UNPROCESSED)
    def _passthrough(colcon_args: tuple[str, ...]) -> None:
        try:
            ws_str = os.environ.get("CWM_WORKSPACE")
            if not ws_str:
                raise NotActivatedError(
                    f"cwm {verb} requires an active CWM workspace.\n"
                    "  Activate:  source <(cwm activate <branch>)"
                )
            run_colcon(verb, Path(ws_str), list(colcon_args))
        except CWMError as exc:
            raise click.ClickException(str(exc)) from exc

    return _passthrough


def _make_moved_command(old_name: str, new_path: str) -> click.Command:
    """Return a hidden compatibility stub that reports the new command path."""

    @click.command(
        name=old_name,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
        help=f"Moved to ``cwm {new_path}``.",
        hidden=True,
    )
    @click.argument("_args", nargs=-1, type=click.UNPROCESSED)
    def _moved(_args: tuple[str, ...]) -> None:
        raise click.ClickException(
            f"'cwm {old_name}' was removed.\n"
            f"Use: cwm {new_path}"
        )

    return _moved


@click.group(cls=HelpfulGroup)
@click.version_option(version=__version__, prog_name="cwm")
def cli() -> None:
    """Colcon Worktree Manager - parallel ROS 2 development with git worktrees."""


cli.command_order = [
    "init",
    "activate",
    "deactivate",
    "switch",
    "cd",
    "shell-init",
    "worktree",
    "repo",
    "ws",
    "inspect",
    "base",
    "doctor",
]
cli.enable_passthrough = True
cli.moved_commands = {
    "build": "ws build",
    "clean": "ws clean",
    "status": "ws status",
    "env": "inspect env",
    "detect": "inspect detect",
    "rm": "worktree remove",
}


@cli.group()
def worktree() -> None:
    """Manage worktree overlay workspaces."""


worktree.command_order = ["add", "focus", "remove", "list", "prune"]


@cli.group()
def ws() -> None:
    """Manage workspace operations."""


ws.command_order = ["build", "test", "test-result", "clean", "status"]


@cli.group()
def inspect() -> None:
    """Inspect CWM state for humans and tooling."""


inspect.command_order = ["env", "detect", "changed", "graph"]


@cli.group()
def base() -> None:
    """Manage the base (underlay) workspace."""


base.command_order = ["update", "build", "clean", "status", "doctor"]


# Import subcommands so they register with Click groups.
# The imports are intentionally at the bottom to avoid circular dependencies.
import cwm.cli.init_cmd  # noqa: E402, F401
import cwm.cli.activate_cmd  # noqa: E402, F401
import cwm.cli.cd_cmd  # noqa: E402, F401
import cwm.cli.shell_init_cmd  # noqa: E402, F401
import cwm.cli.worktree_cmd  # noqa: E402, F401
import cwm.cli.repo_cmd  # noqa: E402, F401
import cwm.cli.build_cmd  # noqa: E402, F401
import cwm.cli.test_cmd  # noqa: E402, F401
import cwm.cli.clean_cmd  # noqa: E402, F401
import cwm.cli.status_cmd  # noqa: E402, F401
import cwm.cli.env_cmd  # noqa: E402, F401
import cwm.cli.detect_cmd  # noqa: E402, F401
import cwm.cli.inspect_changed_cmd  # noqa: E402, F401
import cwm.cli.inspect_graph_cmd  # noqa: E402, F401
import cwm.cli.base_cmd  # noqa: E402, F401
import cwm.cli.doctor_cmd  # noqa: E402, F401
