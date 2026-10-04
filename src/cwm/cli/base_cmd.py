"""cwm base - manage the base (underlay) workspace.

Commands:
- ``update``  pull every repository in the default set and rebuild the base workspace
- ``build``   rebuild the base workspace without pulling
- ``clean``   remove the base build artifacts (build/, install/, log/)
- ``status``  show the base workspace state
- ``doctor``  diagnose (and optionally fix) stale build directories
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import click

from cwm.cli.clean_cmd import _artifact_dirs
from cwm.cli.completion import suppress_completion
from cwm.cli.main import base
from cwm.cli.status_cmd import _collect_base, _print_base_status
from cwm.core.config import Config
from cwm.errors import CWMError, NoRepoSelectedError, UnderlayNotFoundError
from cwm.util import git
from cwm.util.colcon_runner import run_colcon_sourced
from cwm.util.filesystem import find_project_root


def _build_base(config: Config, colcon_args: list[str]) -> None:
    """Rebuild the base workspace, sourcing the ROS 2 underlay first.

    The underlay (``config.underlay``, e.g. /opt/ros/jazzy) is the ROS distro
    setup; the base workspace itself is the build output, so no overlay is
    sourced.
    """
    underlay = Path(config.underlay)
    setup = underlay / "setup.bash"
    if not setup.exists():
        raise UnderlayNotFoundError(
            f"Underlay setup not found: {setup}\n"
            "Check 'underlay' in .cwm/config.yaml (e.g. /opt/ros/jazzy), then re-run."
        )

    build_args: list[str] = []
    if config.symlink_install:
        build_args.append("--symlink-install")
    build_args.extend(colcon_args)

    run_colcon_sourced("build", config.project_root, underlay, None, build_args)


@base.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.option(
    "--no-build",
    is_flag=True,
    help="Only pull the latest changes without building.",
)
@click.argument("colcon_args", nargs=-1, type=click.UNPROCESSED, shell_complete=suppress_completion)
def update(no_build: bool, colcon_args: tuple[str, ...]) -> None:
    """Pull every repository in the default set and rebuild the base workspace.

    Each repository is pulled independently and reported on its own line; if
    any pull fails, the build is skipped and the command exits non-zero.
    Extra arguments are forwarded to ``colcon build``.
    """
    try:
        root = find_project_root()
        config = Config.load(root)

        if not config.repos:
            raise NoRepoSelectedError(
                "No repository selected.\n"
                "Run: cwm repo add <path>"
            )

        failures: list[str] = []
        for rel, repo_path in config.repo_paths.items():
            if not repo_path.exists():
                click.echo(f"  {rel}: " + click.style("not found", fg="red") + f" ({repo_path})")
                failures.append(rel)
                continue
            click.echo(f"Pulling {rel}...")
            try:
                git.pull(cwd=repo_path)
            except CWMError as exc:
                click.echo(f"  {rel}: " + click.style("pull failed", fg="red"))
                click.echo(f"    {exc}")
                failures.append(rel)
                continue
            click.echo(f"  {rel}: " + click.style("pulled", fg="green"))

        if failures:
            raise CWMError(
                f"Pull failed for: {', '.join(failures)}. Base workspace was not rebuilt."
            )
        click.echo("  Pull complete.")

        if no_build:
            click.echo("Skipping build (--no-build).")
            return

        click.echo("Building base workspace...")
        _build_base(config, list(colcon_args))
        click.echo("Base workspace updated successfully.")

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


@base.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("colcon_args", nargs=-1, type=click.UNPROCESSED, shell_complete=suppress_completion)
def build(colcon_args: tuple[str, ...]) -> None:
    """Rebuild the base workspace without pulling.

    Sources the ROS 2 underlay before invoking colcon (symmetric with the
    worktree build path). Extra arguments are forwarded to ``colcon build``.
    """
    try:
        root = find_project_root()
        config = Config.load(root)

        click.echo("Building base workspace...")
        _build_base(config, list(colcon_args))
        click.echo("Base workspace built successfully.")

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


@base.command()
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def clean(yes: bool) -> None:
    """Remove the base build artifacts (build/, install/, log/).

    Every worktree overlays the base install as its underlay, so they will all
    need rebuilding afterwards.
    """
    try:
        root = find_project_root()
        config = Config.load(root)

        dirs = [d for d in _artifact_dirs(config.project_root) if d.is_dir()]
        if not dirs:
            click.echo("Nothing to clean.")
            return

        click.echo("Warning: all worktrees depend on the base install and will need rebuilding.")
        if not yes and not click.confirm("Remove the base build artifacts?"):
            click.echo("Aborted.")
            return

        for d in dirs:
            click.echo(f"  Removing {d}")
            shutil.rmtree(d)
        click.echo("Base workspace cleaned.")

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


@base.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (for scripting/agents).")
def status(as_json: bool) -> None:
    """Show the state of the base workspace."""
    try:
        root = find_project_root()
        config = Config.load(root)

        info = _collect_base(config)
        if as_json:
            click.echo(json.dumps(info, indent=2))
            return

        _print_base_status(info)

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


def _parse_cmake_home(cache: Path) -> Path | None:
    """Return CMAKE_HOME_DIRECTORY from a CMakeCache.txt, or None if absent/unreadable."""
    try:
        for line in cache.read_text(errors="ignore").splitlines():
            if line.startswith("CMAKE_HOME_DIRECTORY:"):
                return Path(line.partition("=")[2].strip())
    except OSError:
        return None
    return None


def _scan_stale_build_dirs(build_root: Path) -> list[tuple[Path, Path]]:
    """Find build/<pkg> dirs whose CMakeCache.txt points at a non-existent source.

    Returns a list of (build_dir, missing_source_dir). Build dirs without a
    CMakeCache.txt (e.g. pure-Python packages) are ignored.
    """
    stale: list[tuple[Path, Path]] = []
    if not build_root.is_dir():
        return stale
    for d in sorted(p for p in build_root.iterdir() if p.is_dir()):
        cache = d / "CMakeCache.txt"
        if not cache.exists():
            continue
        src = _parse_cmake_home(cache)
        if src is not None and not src.exists():
            stale.append((d, src))
    return stale


@base.command()
@click.option("--fix", is_flag=True, help="Delete stale build directories.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (for scripting/agents).")
def doctor(fix: bool, as_json: bool) -> None:
    """Diagnose the base workspace.

    Reports the underlay, whether the base is built, and stale build directories
    (build/<pkg> whose CMakeCache points at a source that no longer exists, e.g.
    a moved or deleted package). With --fix, deletes those stale directories.
    """
    try:
        root = find_project_root()
        config = Config.load(root)

        underlay = Path(config.underlay)
        underlay_ok = (underlay / "setup.bash").exists()
        base_built = (config.base_install_path / "setup.bash").exists()
        stale = _scan_stale_build_dirs(config.project_root / "build")

        removed: list[str] = []
        if fix:
            for build_dir, _src in stale:
                shutil.rmtree(build_dir)
                removed.append(str(build_dir))

        if as_json:
            click.echo(json.dumps({
                "underlay": str(underlay),
                "underlay_ok": underlay_ok,
                "base_built": base_built,
                "stale_build_dirs": [
                    {"build_dir": str(b), "missing_source": str(s)} for b, s in stale
                ],
                "fixed": removed,
            }, indent=2))
            return

        underlay_mark = click.style("ok", fg="green") if underlay_ok else click.style("missing", fg="red")
        click.echo(f"Underlay        {underlay}  {underlay_mark}")
        built_mark = click.style("built", fg="green") if base_built else click.style("not built", fg="yellow")
        click.echo(f"Base install    {built_mark}")

        if not stale:
            click.echo(click.style("No stale build directories.", fg="green"))
            return

        click.echo()
        click.echo(f"Stale build directories ({len(stale)}):")
        for build_dir, src in stale:
            click.echo(f"  {build_dir.name}  -> missing source {src}")
            if fix:
                click.echo(f"    {click.style('removed', fg='yellow')} {build_dir}")
        if not fix:
            click.echo()
            click.echo("Run 'cwm base doctor --fix' to delete these directories.")

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc
