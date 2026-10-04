"""Colcon Discovery Controller - detect changed packages and control colcon discovery."""

from __future__ import annotations

from pathlib import Path

from cwm.core.dependency_graph import DependencyGraphAnalyzer
from cwm.util import git


class ColconDiscoveryController:
    """Analyses git diff to identify changed packages and generates optimised
    colcon build arguments.

    The primary strategy uses ``--packages-select`` + ``--allow-overriding``
    to limit builds to only the affected packages. A COLCON_IGNORE fallback
    is available for edge cases.
    """

    def __init__(self, worktree_src: Path) -> None:
        self._src = worktree_src

    # -- Changed file detection ------------------------------------------------

    def get_changed_files_meta(
        self,
        sub_repos: list[str],
        sub_repo_shas: dict[str, str],
    ) -> list[str]:
        """Return files changed across multiple sub-repositories.

        For each sub-repo in *sub_repos*, runs ``git diff`` against the SHA
        recorded at worktree creation time (*sub_repo_shas*).  File paths are
        prefixed with the sub-repo's relative path so they match the layout of
        the worktree ``src/`` directory.

        Example return value::

            ["core/autoware_core/pkg_a/src/main.cpp",
             "core/autoware_core/pkg_b/include/foo.hpp"]
        """
        changed: list[str] = []
        for rel in sub_repos:
            sub_dir = self._src / rel
            if not sub_dir.is_dir():
                continue
            base_sha = sub_repo_shas.get(rel) or "HEAD~1"
            files = git.diff_name_only(base_sha, cwd=sub_dir)
            changed.extend(f"{rel}/{f}" for f in files)
        return changed

    def get_changed_packages(
        self,
        graph: DependencyGraphAnalyzer,
        changed_files: list[str] | None = None,
    ) -> set[str]:
        """Map changed files to the ROS packages that contain them.

        A file belongs to a package if it resides under that package's source
        directory (the directory containing ``package.xml``).

        *changed_files* may be provided directly (e.g. from
        :meth:`get_changed_files_meta`) to skip the default git-diff lookup.
        """
        if changed_files is None:
            changed_files = self.get_changed_files()
        if not changed_files:
            return set()

        # Pre-resolve package paths once, sorted longest-first for greedy match
        resolved_src = self._src.resolve()
        pkg_entries = sorted(
            (
                (name, graph.package_path(name).resolve().relative_to(resolved_src))
                for name in graph.packages
            ),
            key=lambda e: len(e[1].parts),
            reverse=True,
        )

        changed_pkgs: set[str] = set()
        for filepath in changed_files:
            file_path = Path(filepath)
            for pkg_name, pkg_rel in pkg_entries:
                try:
                    file_path.relative_to(pkg_rel)
                    changed_pkgs.add(pkg_name)
                    break
                except ValueError:
                    continue

        return changed_pkgs

    # -- Colcon argument generation --------------------------------------------

    def generate_build_args(
        self,
        changed_pkgs: set[str],
        affected_pkgs: set[str],
        *,
        symlink_install: bool = True,
    ) -> list[str]:
        """Generate colcon build arguments for the affected package set.

        *changed_pkgs* are packages directly modified by the developer.
        *affected_pkgs* are reverse dependencies that must also be rebuilt
        for ABI safety (as computed by DependencyGraphAnalyzer).

        The combined set is passed to ``--packages-select`` and
        ``--allow-overriding``.
        """
        all_pkgs = sorted(changed_pkgs | affected_pkgs)
        if not all_pkgs:
            return []

        args = [
            "--packages-select", *all_pkgs,
            "--allow-overriding", *all_pkgs,
        ]
        if symlink_install:
            args.append("--symlink-install")
        return args

    def generate_test_args(
        self,
        changed_pkgs: set[str],
        affected_pkgs: set[str],
    ) -> list[str]:
        """Generate colcon test arguments for the changed+affected package set.

        Unlike :meth:`generate_build_args`, ``colcon test`` has no
        ``--allow-overriding`` / ``--symlink-install``; only ``--packages-select``
        applies.
        """
        all_pkgs = sorted(changed_pkgs | affected_pkgs)
        if not all_pkgs:
            return []
        return ["--packages-select", *all_pkgs]

    # -- COLCON_IGNORE fallback ------------------------------------------------

    def place_ignore_markers(self, graph: DependencyGraphAnalyzer, keep: set[str]) -> list[Path]:
        """Place COLCON_IGNORE markers in all packages NOT in *keep*.

        Returns the list of created marker paths for later cleanup.
        """
        markers: list[Path] = []
        for pkg_name in graph.packages:
            if pkg_name not in keep:
                marker = graph.package_path(pkg_name) / "COLCON_IGNORE"
                marker.touch()
                markers.append(marker)
        return markers

    @staticmethod
    def remove_ignore_markers(markers: list[Path]) -> None:
        """Remove previously placed COLCON_IGNORE markers."""
        for marker in markers:
            marker.unlink(missing_ok=True)
