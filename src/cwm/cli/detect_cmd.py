"""cwm inspect detect - detect CWM project and output workspace information."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from cwm.cli.main import inspect
from cwm.core.config import Config
from cwm.errors import ConfigNotFoundError, ConfigVersionError
from cwm.util.filesystem import find_project_root


@inspect.command()
@click.option("--cwd", default=None, type=click.Path(exists=True), help="Directory to search from (default: current directory).")
def detect(cwd: str | None) -> None:
    """Detect whether the current directory is inside a CWM project.

    Outputs project information as JSON for programmatic use by
    external tools such as Kanban.
    """
    start = Path(cwd) if cwd else None

    try:
        root = find_project_root(start)
        config = Config.load(root)
    except ConfigVersionError as exc:
        raise click.ClickException(str(exc)) from exc
    except ConfigNotFoundError:
        click.echo(json.dumps({"is_cwm": False}))
        return

    active_worktree = os.environ.get("CWM_WORKTREE")

    result = {
        "is_cwm": True,
        "project_root": str(root),
        "underlay": config.underlay,
        "repos": list(config.repos),
        **({"active_worktree": active_worktree} if active_worktree else {}),
    }

    click.echo(json.dumps(result))
