"""Unit tests for cwm.cli.completion."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import cwm.cli.completion as completion_mod
from cwm.cli.completion import (
    complete_distros,
    complete_git_branches,
    complete_worktree_branches,
)


def _ctx(params: dict | None = None) -> MagicMock:
    ctx = MagicMock(spec=["params"])
    ctx.params = params or {}
    return ctx


def _param() -> MagicMock:
    return MagicMock()


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear lru_cache between tests."""
    completion_mod._load_config_and_manager.cache_clear()
    yield
    completion_mod._load_config_and_manager.cache_clear()


class TestCompleteWorktreeBranches:
    def _make_meta(self, branch: str) -> MagicMock:
        m = MagicMock()
        m.branch = branch
        return m

    def test_returns_matching_branches(self):
        manager = MagicMock()
        manager.list_worktrees.return_value = [
            self._make_meta("feature-a"),
            self._make_meta("feature-b"),
            self._make_meta("hotfix-1"),
        ]
        config = MagicMock()

        with patch.object(completion_mod, "_load_config_and_manager", return_value=(config, manager)):
            completion_mod._load_config_and_manager.cache_clear()
            items = complete_worktree_branches(_ctx(), _param(), "feature")

        assert {i.value for i in items} == {"feature-a", "feature-b"}

    def test_returns_all_branches_on_empty_incomplete(self):
        manager = MagicMock()
        manager.list_worktrees.return_value = [
            self._make_meta("main"),
            self._make_meta("dev"),
        ]
        config = MagicMock()

        with patch.object(completion_mod, "_load_config_and_manager", return_value=(config, manager)):
            completion_mod._load_config_and_manager.cache_clear()
            items = complete_worktree_branches(_ctx(), _param(), "")

        assert {i.value for i in items} == {"main", "dev"}

    def test_returns_empty_on_exception(self):
        with patch.object(
            completion_mod, "_load_config_and_manager", side_effect=RuntimeError("no project")
        ):
            completion_mod._load_config_and_manager.cache_clear()
            items = complete_worktree_branches(_ctx(), _param(), "feat")

        assert items == []


class TestCompleteGitBranches:
    def test_returns_matching_branches(self):
        config = MagicMock()
        config.repo_paths = {"my_repo": Path("/fake/src/my_repo")}
        manager = MagicMock()

        with (
            patch.object(completion_mod, "_load_config_and_manager", return_value=(config, manager)),
            patch("cwm.util.git.list_branches", return_value=["main", "dev", "feature-xyz"]),
        ):
            completion_mod._load_config_and_manager.cache_clear()
            items = complete_git_branches(_ctx(), _param(), "feat")

        assert [i.value for i in items] == ["feature-xyz"]

    def test_unions_branches_of_every_default_repo(self):
        config = MagicMock()
        config.repo_paths = {"a": Path("/fake/src/a"), "b": Path("/fake/src/b")}
        branches = {Path("/fake/src/a"): ["feat-a", "main"], Path("/fake/src/b"): ["feat-b", "main"]}

        with (
            patch.object(completion_mod, "_load_config_and_manager", return_value=(config, MagicMock())),
            patch("cwm.util.git.list_branches", side_effect=lambda cwd, include_remote: branches[cwd]),
        ):
            items = complete_git_branches(_ctx(), _param(), "")

        assert [i.value for i in items] == ["feat-a", "feat-b", "main"]

    def test_returns_empty_on_exception(self):
        with patch.object(
            completion_mod, "_load_config_and_manager", side_effect=RuntimeError("no git")
        ):
            completion_mod._load_config_and_manager.cache_clear()
            items = complete_git_branches(_ctx(), _param(), "")

        assert items == []


class TestCompleteDistros:
    def test_returns_matching_distros(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import cwm.util.ros_env as ros_env

        (tmp_path / "jazzy").mkdir()
        (tmp_path / "jazzy" / "setup.bash").write_text("# fake")
        (tmp_path / "humble").mkdir()
        (tmp_path / "humble" / "setup.bash").write_text("# fake")
        monkeypatch.setattr(ros_env, "ROS_INSTALL_BASE", tmp_path)

        items = complete_distros(_ctx(), _param(), str(tmp_path / "j"))

        assert len(items) == 1
        assert items[0].value == str(tmp_path / "jazzy")

    def test_returns_empty_on_exception(self):
        with patch("cwm.util.ros_env.list_available_distros", side_effect=RuntimeError("fail")):
            items = complete_distros(_ctx(), _param(), "")

        assert items == []


class TestCompleteRepoList:
    def test_completes_last_segment_of_comma_list(self, tmp_path: Path):
        from cwm.cli.completion import complete_repo_list
        from cwm.core.config import Config
        from tests.conftest import make_git_repo

        config = Config(project_root=tmp_path)
        for rel in ("repo_a", "repo_b", "group/repo_c"):
            make_git_repo(config.base_src_path / rel)

        with patch.object(completion_mod, "_load_config_and_manager", return_value=(config, MagicMock())):
            items = complete_repo_list(_ctx(), _param(), "repo_a,")

        assert [i.value for i in items] == ["repo_a,group/repo_c", "repo_a,repo_b"]


class TestCompleteWorktreeRepos:
    def test_lists_repos_of_branch(self):
        from cwm.cli.completion import complete_worktree_repos

        manager = MagicMock()
        manager.get_worktree_meta.return_value.repo_names = ["repo_a", "group/repo_b"]
        with patch.object(completion_mod, "_load_config_and_manager", return_value=(MagicMock(), manager)):
            items = complete_worktree_repos(_ctx({"branch": "feat"}), _param(), "g")

        assert [i.value for i in items] == ["group/repo_b"]
