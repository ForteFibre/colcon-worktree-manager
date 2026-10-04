"""Compute environment changes made by bash scripts, and render them for fish.

ROS 2 setup scripts only exist for POSIX shells (setup.bash / setup.zsh /
setup.sh).  To activate a worktree from fish, CWM runs the same bash
statements the bash activation uses in a bash subprocess, dumps the resulting
environment, and diffs it against a baseline taken from an identical bash
invocation that sources nothing.  Diffing two bash runs (rather than the
caller's environment) cancels the variables bash itself sets (SHLVL, PWD, _).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field

from cwm.errors import CWMError

# Only variables with names fish can address are carried over; this drops
# bash's exported functions ('BASH_FUNC_name%%') and other exotic entries.
_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Per-process bookkeeping variables that differ between any two shells and must
# never be copied into the caller's environment.
_IGNORED_VARS = frozenset({"_", "SHLVL", "PWD", "OLDPWD"})


@dataclass
class EnvChanges:
    """Variables set/changed and variables removed by a script."""

    set_vars: dict[str, str] = field(default_factory=dict)
    unset_vars: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        """Every variable name touched, set ones first (insertion order)."""
        return [*self.set_vars, *self.unset_vars]


def _parse_env0(raw: bytes) -> dict[str, str]:
    env: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        name, _, value = entry.partition(b"=")
        key = name.decode("utf-8", "surrogateescape")
        if _VALID_NAME.match(key) and key not in _IGNORED_VARS:
            env[key] = value.decode("utf-8", "surrogateescape")
    return env


def _bash_env(bash: str, script: str, env: Mapping[str, str]) -> tuple[dict[str, str], str]:
    """Run *script* in a clean, non-interactive bash and return (environment, stderr).

    The script's own stdout is redirected to stderr so chatty setup scripts
    cannot corrupt the NUL-separated environment dump on stdout.
    """
    wrapped = f"{{\n{script}\n}} >&2\nenv -0\n"
    try:
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", wrapped],
            env=dict(env),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise CWMError(f"Failed to run bash for environment capture: {exc}") from exc
    stderr = result.stderr.decode("utf-8", "replace")
    if result.returncode != 0:
        raise CWMError(
            f"bash exited with status {result.returncode} while capturing the activation "
            f"environment:\n{stderr.strip()}"
        )
    return _parse_env0(result.stdout), stderr


def capture_env_changes(
    script: str, *, env: Mapping[str, str] | None = None
) -> tuple[EnvChanges, str]:
    """Return the environment changes *script* makes when run by bash, plus its stderr.

    *env* defaults to the current process environment.
    """
    bash = shutil.which("bash")
    if bash is None:
        raise CWMError(
            "bash not found on PATH. Activating from fish runs the ROS 2 setup "
            "scripts (bash-only) in a bash subprocess."
        )
    base_env = dict(os.environ if env is None else env)
    before, _ = _bash_env(bash, ":", base_env)
    after, stderr = _bash_env(bash, script, base_env)

    # Sorted so the generated script is stable across runs.
    changes = EnvChanges()
    for name in sorted(after):
        if before.get(name) != after[name]:
            changes.set_vars[name] = after[name]
    changes.unset_vars = sorted(name for name in before if name not in after)
    return changes, stderr


def fish_quote(value: str) -> str:
    """Quote *value* as a single fish word.

    Inside fish single quotes only backslash and the single quote are special;
    newlines and every other character are taken literally.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
