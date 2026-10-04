"""Fingerprint a worktree's overlay install to detect a stale activation.

Activation sources the overlay's ``local_setup.bash`` once.  colcon's setup
scripts enumerate the installed packages at *sourcing* time, so the shell keeps
the old environment when the overlay did not exist yet (first build after
activation) or when a later build installs new packages.  Activation records
:func:`overlay_fingerprint` in ``CWM_OVERLAY_FP``; comparing it with the current
fingerprint after ``cwm ws build`` tells whether the shell must re-activate.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: Environment variable holding the fingerprint taken at activation time.
OVERLAY_FP_VAR = "CWM_OVERLAY_FP"

#: Fingerprint of an overlay whose ``local_setup.bash`` does not exist yet.
NO_OVERLAY = "none"

_INDEX = Path("share") / "colcon-core" / "packages"


def _package_index(install: Path) -> dict[str, Path]:
    """Return {package name: install prefix} from colcon's package index.

    Supports both layouts: merged (``install/share/colcon-core/packages/<pkg>``)
    and isolated (``install/<pkg>/share/colcon-core/packages/<pkg>``).
    """
    packages: dict[str, Path] = {}
    merged = install / _INDEX
    if merged.is_dir():
        for entry in merged.iterdir():
            packages[entry.name] = install
    for entry in install.glob(f"*/{_INDEX.as_posix()}/*"):
        prefix = entry.parents[3]
        if entry.name == prefix.name:
            packages[entry.name] = prefix
    return packages


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def overlay_fingerprint(install: Path) -> str:
    """Return a short fingerprint of what sourcing *install*/local_setup.bash sets up.

    Covers the set of installed packages, their runtime dependencies (which
    order the sourcing) and each package's ``package.dsv`` hook list, i.e. the
    inputs colcon's ``local_setup`` reads.  Rebuilding existing packages
    leaves it unchanged.  Returns :data:`NO_OVERLAY` when the overlay has not
    been built yet.
    """
    if not (install / "local_setup.bash").is_file():
        return NO_OVERLAY
    digest = hashlib.sha256()
    for name, prefix in sorted(_package_index(install).items()):
        digest.update(name.encode() + b"\0")
        digest.update(_read(prefix / _INDEX / name) + b"\0")
        digest.update(_read(prefix / "share" / name / "package.dsv") + b"\0")
    return digest.hexdigest()[:16]


def overlay_changed_since_activation(install: Path) -> bool:
    """Return True if *install* differs from the fingerprint recorded at activation.

    Returns False when no fingerprint was recorded (no active worktree, or a
    shell activated by an older cwm), since nothing can be compared.
    """
    recorded = os.environ.get(OVERLAY_FP_VAR)
    if not recorded:
        return False
    return overlay_fingerprint(install) != recorded
