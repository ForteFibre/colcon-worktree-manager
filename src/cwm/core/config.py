"""CWM project configuration stored in .cwm/config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from cwm.errors import ConfigNotFoundError, ConfigVersionError

CONFIG_DIR = ".cwm"
CONFIG_FILE = "config.yaml"
WORKTREES_META_DIR = "worktrees"
CACHE_DIR = "cache"
COLCON_IGNORE = "COLCON_IGNORE"

CONFIG_VERSION = 4
# Oldest config version that can still be migrated in memory on load.  v3
# tracked a single repository ('repo: <path>'); v4 tracks a default set
# ('repos: [<path>, ...]').
MIN_MIGRATABLE_VERSION = 3


@dataclass
class Config:
    """Top-level CWM configuration."""

    version: int = CONFIG_VERSION
    underlay: str = ""
    symlink_install: bool = True
    worktrees_dir: str = "worktrees"
    # Default set of git repositories (paths relative to src/) checked out into
    # a new worktree when 'cwm worktree add' is run without --repos.
    repos: list[str] = field(default_factory=list)

    # Runtime-only (not serialised)
    project_root: Path = field(default=Path("."), repr=False)

    # -- Serialisation ---------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise to a plain dict (excluding runtime fields)."""
        d: dict = {
            "version": self.version,
            "underlay": self.underlay,
            "symlink_install": self.symlink_install,
            "worktrees_dir": self.worktrees_dir,
            "repos": list(self.repos),
        }
        return d

    @classmethod
    def from_dict(cls, data: dict, project_root: Path) -> Config:
        """Deserialise from a plain dict.

        v3 configs carry a single ``repo`` key; it is migrated to a one-element
        ``repos`` list.  The migrated config is written back as v4 on the next
        :meth:`save`.
        """
        repos = data.get("repos")
        if repos is None:
            legacy = data.get("repo")
            repos = [legacy] if legacy else []
        return cls(
            version=CONFIG_VERSION,
            underlay=data.get("underlay", "/opt/ros/jazzy"),
            symlink_install=data.get("symlink_install", True),
            worktrees_dir=data.get("worktrees_dir", "worktrees"),
            repos=[str(r) for r in repos if r],
            project_root=project_root,
        )

    # -- Persistence -----------------------------------------------------------

    def save(self) -> None:
        """Write the config to .cwm/config.yaml."""
        config_path = self.project_root / CONFIG_DIR / CONFIG_FILE
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as fh:
            yaml.safe_dump(self.to_dict(), fh, default_flow_style=False)

    @classmethod
    def load(cls, project_root: Path) -> Config:
        """Load configuration from *project_root*/.cwm/config.yaml."""
        config_path = project_root / CONFIG_DIR / CONFIG_FILE
        if not config_path.exists():
            raise ConfigNotFoundError(f"Config not found: {config_path}")
        with open(config_path) as fh:
            data = yaml.safe_load(fh) or {}

        version = data.get("version", 1)
        if version < MIN_MIGRATABLE_VERSION:
            raise ConfigVersionError(
                f"CWM config at {config_path} uses version {version} (current: {CONFIG_VERSION}).\n"
                "The config schema has changed: tracked repositories are now listed explicitly.\n"
                "Please re-initialise with: cwm init"
            )

        return cls.from_dict(data, project_root)

    # -- Derived paths ---------------------------------------------------------

    @property
    def cwm_dir(self) -> Path:
        return self.project_root / CONFIG_DIR

    @staticmethod
    def checkout_name(repo: str) -> str:
        """Directory name of *repo*'s checkout inside a worktree's src/.

        Worktrees flatten repositories to their basename
        (``core/autoware_core`` -> ``src/autoware_core``), so two repositories
        with the same basename cannot share a worktree.
        """
        return Path(repo).name

    def repo_path(self, repo: str) -> Path:
        """Absolute path to the base checkout of *repo* (relative to src/)."""
        return self.base_src_path / repo

    @property
    def repo_paths(self) -> dict[str, Path]:
        """Mapping of each default repository to its absolute base path."""
        return {r: self.repo_path(r) for r in self.repos}

    @property
    def base_src_path(self) -> Path:
        return self.project_root / "src"

    @property
    def base_install_path(self) -> Path:
        return self.project_root / "install"

    @property
    def worktrees_path(self) -> Path:
        return self.project_root / self.worktrees_dir

    @staticmethod
    def safe_branch_name(branch: str) -> str:
        """Sanitise a branch name for use as a directory/file name."""
        return branch.replace("/", "-")

    def worktree_ws_path(self, branch: str) -> Path:
        """Return the workspace root for a given branch worktree."""
        return self.worktrees_path / f"{self.safe_branch_name(branch)}_ws"

    def worktree_src_path(self, branch: str) -> Path:
        return self.worktree_ws_path(branch) / "src"

    def worktree_checkout_path(self, branch: str, repo: str) -> Path:
        """Return *repo*'s git worktree checkout inside the *branch* workspace."""
        return self.worktree_src_path(branch) / self.checkout_name(repo)

    def worktree_install_path(self, branch: str) -> Path:
        return self.worktree_ws_path(branch) / "install"

    def worktree_meta_path(self, branch: str) -> Path:
        return self.cwm_dir / WORKTREES_META_DIR / f"{self.safe_branch_name(branch)}.yaml"

    @property
    def cache_path(self) -> Path:
        return self.cwm_dir / CACHE_DIR

    @property
    def dag_cache_dir(self) -> Path:
        return self.cache_path / "dag"

    def ensure_worktrees_ignore_marker(self) -> None:
        self.worktrees_path.mkdir(parents=True, exist_ok=True)
        (self.worktrees_path / COLCON_IGNORE).touch()
