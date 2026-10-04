"""Tests for cwm.core.changeset.compute_changeset.

The pipeline's building blocks (scan / reverse-deps / topological sort / file
diff) are covered directly in test_dga.py and test_cdc.py.  Here we patch those
class methods and verify only how compute_changeset wires them together.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from cwm.core.changeset import compute_changeset
from cwm.core.worktree_state import RepoState


def _mock_config(tmp_path: Path) -> MagicMock:
    config = MagicMock()
    config.worktree_src_path.return_value = tmp_path / "src"
    config.worktree_meta_path.return_value = tmp_path / "meta.yaml"
    return config


class TestComputeChangeset:
    def test_includes_reverse_deps_in_topological_order(self, tmp_path: Path) -> None:
        config = _mock_config(tmp_path)
        with patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.scan"), \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.packages",
                   new_callable=PropertyMock, return_value={"a", "b", "c"}), \
             patch("cwm.core.colcon_discovery.ColconDiscoveryController.get_changed_files_meta",
                   return_value=["repo/a/src/main.cpp"]) as mock_files, \
             patch("cwm.core.colcon_discovery.ColconDiscoveryController.get_changed_packages",
                   return_value={"a"}), \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.get_reverse_deps",
                   return_value={"b"}), \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.topological_sort",
                   side_effect=sorted), \
             patch("cwm.core.worktree_state.WorktreeMeta.load") as mock_meta:
            mock_meta.return_value.repos = {"group/repo": RepoState(base_sha="abc123")}

            changeset = compute_changeset(config, "feature-x")

        # Repos are diffed by checkout (basename) against their own base SHA.
        mock_files.assert_called_once_with(["repo"], {"repo": "abc123"})
        assert changeset.package_count == 3
        assert changeset.changed == {"a"}
        assert changeset.affected == {"b"}
        assert changeset.build_order == ["a", "b"]

    def test_no_rdeps_skips_reverse_dependency_analysis(self, tmp_path: Path) -> None:
        config = _mock_config(tmp_path)
        with patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.scan"), \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.packages",
                   new_callable=PropertyMock, return_value={"a", "b"}), \
             patch("cwm.core.colcon_discovery.ColconDiscoveryController.get_changed_files_meta",
                   return_value=["repo/a/src/main.cpp"]), \
             patch("cwm.core.colcon_discovery.ColconDiscoveryController.get_changed_packages",
                   return_value={"a"}), \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.get_reverse_deps") as mock_rev, \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.topological_sort",
                   side_effect=sorted), \
             patch("cwm.core.worktree_state.WorktreeMeta.load") as mock_meta:
            mock_meta.return_value.repos = {"group/repo": RepoState(base_sha="abc123")}

            changeset = compute_changeset(config, "feature-x", no_rdeps=True)

        mock_rev.assert_not_called()
        assert changeset.affected == set()
        assert changeset.build_order == ["a"]

    def test_no_changes_yields_empty_build_order(self, tmp_path: Path) -> None:
        config = _mock_config(tmp_path)
        with patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.scan"), \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.packages",
                   new_callable=PropertyMock, return_value={"a", "b"}), \
             patch("cwm.core.colcon_discovery.ColconDiscoveryController.get_changed_files_meta",
                   return_value=[]), \
             patch("cwm.core.colcon_discovery.ColconDiscoveryController.get_changed_packages",
                   return_value=set()), \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.get_reverse_deps",
                   return_value=set()), \
             patch("cwm.core.dependency_graph.DependencyGraphAnalyzer.topological_sort",
                   side_effect=sorted), \
             patch("cwm.core.worktree_state.WorktreeMeta.load") as mock_meta:
            mock_meta.return_value.repos = {"group/repo": RepoState(base_sha="abc123")}

            changeset = compute_changeset(config, "feature-x")

        assert changeset.changed == set()
        assert changeset.affected == set()
        assert changeset.build_order == []
