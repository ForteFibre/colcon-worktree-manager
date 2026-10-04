"""cwm activate - output a shell activation script for a worktree."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import click

from cwm.cli.completion import complete_worktree_branches
from cwm.cli.main import cli
from cwm.core.config import Config
from cwm.errors import CWMError, WorktreeNotFoundError
from cwm.util.filesystem import find_project_root


# Environment variables that ROS/colcon sourcing will mutate and that the
# generated deactivate() function must restore.
_SNAPSHOT_VARS = (
    "PATH",
    "LD_LIBRARY_PATH",
    "PYTHONPATH",
    "CMAKE_PREFIX_PATH",
    "AMENT_PREFIX_PATH",
    "COLCON_PREFIX_PATH",
    "ROS_DISTRO",
    "ROS_VERSION",
    "ROS_PYTHON_VERSION",
    "ROS_LOCALHOST_ONLY",
    "ROS_DOMAIN_ID",
    "ROS_AUTOMATIC_DISCOVERY_RANGE",
)

_TTY_HINT = """\
cwm activate requires shell integration to mutate the current shell.

  Set up once (add to ~/.bashrc):
    eval "$(cwm shell-init)"

  Then use directly:
    cwm activate <branch>   # activate a specific worktree
    cwm activate            # interactive selection
    cwm deactivate          # restore previous environment
"""

_DEACTIVATE_HINT = """\
cwm deactivate requires shell integration to restore the current shell.

  Set up once (add to ~/.bashrc):
    eval "$(cwm shell-init)"

  Then, after 'cwm activate <branch>':
    cwm deactivate          # restore the previous environment
"""


def _bash_completion_script() -> str:
    """Return the cwm bash completion script using Click's API directly."""
    try:
        from click.shell_completion import BashComplete
        return BashComplete(cli, {}, "cwm", "_CWM_COMPLETE").source()
    except Exception:
        return ""


def generate_activate_script(
    branch: str,
    project_root: str,
    workspace: str,
    underlay: str,
    base_install: str,
    overlay_install: str,
    ros_domain_id: int | None = None,
) -> str:
    """Return a bash activation script for the given worktree.

    Intended to be consumed via: source <(cwm activate <branch>)

    *ros_domain_id* is the worktree's leased ROS_DOMAIN_ID; when given, it is
    exported together with ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST so nodes
    from different worktrees (and other hosts) stay isolated.
    """
    q_branch = shlex.quote(branch)
    q_root = shlex.quote(project_root)
    q_workspace = shlex.quote(workspace)
    q_underlay = shlex.quote(underlay)
    q_base_install = shlex.quote(base_install)
    q_overlay_install = shlex.quote(overlay_install)

    # Build snapshot/restore blocks for the env vars ROS sourcing mutates.
    save_lines = []
    restore_lines = []
    for var in _SNAPSHOT_VARS:
        save_lines.append(
            f'    if [ -n "${{{var}+x}}" ]; then\n'
            f'        export _CWM_OLD_{var}="${{{var}}}"\n'
            f'    else\n'
            f'        unset _CWM_OLD_{var}\n'
            f'        export _CWM_WAS_UNSET_{var}=1\n'
            f'    fi'
        )
        restore_lines.append(
            f'    if [ -n "${{_CWM_WAS_UNSET_{var}+x}}" ]; then\n'
            f'        unset {var}\n'
            f'        unset _CWM_WAS_UNSET_{var}\n'
            f'    elif [ -n "${{_CWM_OLD_{var}+x}}" ]; then\n'
            f'        export {var}="${{_CWM_OLD_{var}}}"\n'
            f'        unset _CWM_OLD_{var}\n'
            f'    fi'
        )

    save_block = "\n".join(save_lines)
    restore_block = "\n".join(restore_lines)

    completion_script = _bash_completion_script()

    if ros_domain_id is not None:
        domain_block = (
            f"export ROS_DOMAIN_ID={int(ros_domain_id)}\n"
            "export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST"
        )
        domain_echo = f'echo "  ROS_DOMAIN_ID: {int(ros_domain_id)} (discovery: LOCALHOST)"\n'
    else:
        domain_block = "# No ROS_DOMAIN_ID leased for this worktree."
        domain_echo = ""

    return f"""\
# cwm activation script - source this file, do not execute it directly.
# Usage: source <(cwm activate {q_branch})

# Auto-deactivate any currently active workspace before activating a new one.
if [ -n "${{CWM_ACTIVE+x}}" ]; then
    if type deactivate >/dev/null 2>&1; then
        deactivate
    fi
fi

# Snapshot environment variables that ROS/colcon sourcing will mutate.
{save_block}
export _CWM_OLD_PS1="${{PS1:-}}"

# Source ROS 2 distro underlay.
if [ -f {q_underlay}/setup.bash ]; then
    source {q_underlay}/setup.bash
fi

# Source base workspace install.
if [ -f {q_base_install}/setup.bash ]; then
    source {q_base_install}/setup.bash
fi

# Source overlay workspace (skipped silently before first build).
if [ -f {q_overlay_install}/local_setup.bash ]; then
    source {q_overlay_install}/local_setup.bash
fi

# Prepend the CWM bin shim to PATH so that subprocess-level 'git' calls (which
# bypass the shell function from 'cwm shell-init') are still intercepted.
export PATH={q_root}/.cwm/bin:${{PATH}}

# Export CWM workspace markers.
export CWM_ACTIVE=1
export CWM_PROJECT_ROOT={q_root}
export CWM_WORKTREE={q_branch}
export CWM_WORKSPACE={q_workspace}

# Isolate ROS 2 discovery per worktree.
{domain_block}

# Modify the shell prompt.
if [ -n "${{PS1+x}}" ]; then
    export PS1="[cwm:{q_branch}] ${{PS1}}"
fi

# Define the deactivate function that undoes everything above.
deactivate() {{
{restore_block}
    if [ -n "${{_CWM_OLD_PS1+x}}" ]; then
        export PS1="${{_CWM_OLD_PS1}}"
        unset _CWM_OLD_PS1
    fi
    unset CWM_ACTIVE CWM_PROJECT_ROOT CWM_WORKTREE CWM_WORKSPACE
    unset -f deactivate
}}

# cwm shell completion - embedded at activation time.
{completion_script}

echo ""
echo "=== CWM Worktree: {q_branch} ==="
echo "  Workspace: {q_workspace}"
{domain_echo}echo "  Run 'cwm ws build' to build changed packages."
echo "  Run 'deactivate' to restore the previous environment."
echo ""
"""


def _list_existing_worktrees(config: Config) -> list[str]:
    """Return branch names of existing worktrees, sorted."""
    from cwm.core.worktree_state import WorktreeStateManager
    manager = WorktreeStateManager(config)
    return [m.branch for m in manager.list_worktrees()]


def _interactive_select(config: Config) -> tuple[str, bool] | None:
    """Show an interactive menu on /dev/tty and return (branch, is_new).

    Returns None if the user cancels.
    is_new=True means the worktree does not exist yet and must be created.
    """
    existing = _list_existing_worktrees(config)

    try:
        tty = open("/dev/tty", "r+")
    except OSError:
        raise click.ClickException(
            "Cannot open /dev/tty for interactive selection. "
            "Provide a branch name: cwm activate <branch>"
        )

    with tty:
        tty.write("\n=== CWM Activate ===\n\n")

        options: list[tuple[str, bool]] = []  # (label, is_new)
        for b in existing:
            options.append((b, False))
        options.append(("[Create new worktree]", True))

        for i, (label, _) in enumerate(options, 1):
            tty.write(f"  {i}. {label}\n")
        tty.write("  0. Cancel\n")
        tty.write("\nSelect: ")
        tty.flush()
        line = tty.readline().strip()

        try:
            idx = int(line)
        except ValueError:
            return None

        if idx == 0 or not 1 <= idx <= len(options):
            return None

        label, is_new = options[idx - 1]

        if not is_new:
            return label, False

        tty.write("New branch name: ")
        tty.flush()
        new_branch = tty.readline().strip()

    if not new_branch:
        return None

    return new_branch, True


def _lease_domain_id(manager, branch: str) -> int | None:
    """Return the worktree's ROS_DOMAIN_ID, leasing one lazily for old worktrees.

    Returns None when the worktree has no metadata (workspace adopted or
    metadata deleted by hand); activation then proceeds without a domain ID.
    """
    try:
        return manager.ensure_domain_id(branch)
    except WorktreeNotFoundError:
        return None


@cli.command()
@click.argument("branch", required=False, default=None, shell_complete=complete_worktree_branches)
def activate(branch: str | None) -> None:
    """Output a shell activation script for the BRANCH worktree.

    With a branch name, outputs the activation script directly:

    \\b
        source <(cwm activate <branch>)

    Without a branch name, shows an interactive menu to pick an existing
    worktree or create a new one:

    \\b
        source <(cwm activate)

    The script sets CWM_ACTIVE, CWM_PROJECT_ROOT, CWM_WORKTREE, CWM_WORKSPACE,
    sources the ROS 2 underlay and workspace overlays, exports the worktree's
    leased ROS_DOMAIN_ID with ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST, and
    defines a 'deactivate' shell function to undo all changes.
    """
    if sys.stdout.isatty():
        raise click.ClickException(_TTY_HINT.rstrip())

    try:
        root = find_project_root()
        config = Config.load(root)

        # Lazily (re)generate the .cwm/bin/git wrapper.  Projects initialised
        # before this feature existed, or where the wrapper was tampered with,
        # need the file to be present before PATH manipulation is meaningful.
        from cwm.core.worktree_state import _write_git_wrapper
        wrapper_path = config.cwm_dir / "bin" / "git"
        if not wrapper_path.is_file() or not os.access(wrapper_path, os.X_OK):
            _write_git_wrapper(wrapper_path)

        is_new = False
        if branch is None:
            result = _interactive_select(config)
            if result is None:
                # User cancelled — output a no-op script so source <(...) succeeds silently.
                click.echo("# cwm activate: cancelled")
                return
            branch, is_new = result
        else:
            ws_path = config.worktree_ws_path(branch)
            if not ws_path.exists():
                raise CWMError(
                    f"Worktree workspace not found: {ws_path}\n"
                    f"Create it first with: cwm worktree add {branch}"
                )

        from cwm.core.worktree_state import WorktreeStateManager
        manager = WorktreeStateManager(config)
        if is_new:
            # Create here (not in the emitted script) so the leased
            # ROS_DOMAIN_ID is known when the script is rendered.
            ws_created = manager.create_worktree(branch)
            click.echo(f"Created worktree workspace: {ws_created}", err=True)

        ws_path = config.worktree_ws_path(branch)
        ros_domain_id = _lease_domain_id(manager, branch)

        script = generate_activate_script(
            branch=branch,
            project_root=str(root),
            workspace=str(ws_path),
            underlay=config.underlay,
            base_install=str(config.base_install_path),
            overlay_install=str(config.worktree_install_path(branch)),
            ros_domain_id=ros_domain_id,
        )

        click.echo(script, nl=False)

    except CWMError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("deactivate")
def deactivate() -> None:
    """Restore the environment saved by 'cwm activate'.

    Requires shell integration. Run 'eval "$(cwm shell-init)"' first; the
    'cwm' shell function then calls the 'deactivate' function defined by
    'cwm activate'. Without shell integration there is no shell state to
    restore, so this command only prints setup guidance.
    """
    raise click.ClickException(_DEACTIVATE_HINT.rstrip())
