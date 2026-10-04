"""Unit tests for cwm.cli.cd_cmd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cwm.cli.cd_cmd import _resolve, cd, cd_resolve, switch
from cwm.core.config import Config
from cwm.core.worktree_state import RepoState, WorktreeMeta
from cwm.errors import CWMError


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Create a minimal CWM project structure."""
    cwm_dir = tmp_path / ".cwm"
    cwm_dir.mkdir()
    (cwm_dir / "worktrees").mkdir()
    config = Config(project_root=tmp_path)
    config.save()
    return tmp_path


@pytest.fixture
def config(project_root: Path) -> Config:
    return Config.load(project_root)


def _make_meta(branch: str, repo: str = "my_repo", *extra: str) -> WorktreeMeta:
    return WorktreeMeta(
        branch=branch,
        created_at="2025-01-01T00:00:00",
        repos={r: RepoState(base_sha="abc123", base_branch="main") for r in (repo, *extra)},
    )


# ---------------------------------------------------------------------------
# _resolve: no arguments
# ---------------------------------------------------------------------------


class TestResolveNoArgs:
    def test_returns_active_workspace(self, project_root: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CWM_WORKSPACE", str(project_root / "worktrees" / "feat_ws"))
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            result = _resolve(())
        assert result == str(project_root / "worktrees" / "feat_ws")

    def test_error_when_no_active_workspace(self, project_root: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("CWM_WORKSPACE", raising=False)
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with pytest.raises(CWMError, match="No worktree specified"):
                _resolve(())


# ---------------------------------------------------------------------------
# _resolve: base
# ---------------------------------------------------------------------------


class TestResolveBase:
    def test_returns_project_root(self, project_root: Path):
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            result = _resolve(("base",))
        assert result == str(project_root)


# ---------------------------------------------------------------------------
# _resolve: branch name
# ---------------------------------------------------------------------------


class TestResolveBranch:
    def test_returns_worktree_root(self, project_root: Path):
        meta = _make_meta("feature-x")
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM:
                MockWSM.return_value.list_worktrees.return_value = [meta]
                result = _resolve(("feature-x",))
        assert result == str(project_root / "worktrees" / "feature-x_ws")

    def test_returns_repo_checkout_with_repo_arg(self, project_root: Path, tmp_path: Path):
        meta = _make_meta("feature-x", repo="my_repo")
        checkout = project_root / "worktrees" / "feature-x_ws" / "src" / "my_repo"
        checkout.mkdir(parents=True)
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM:
                MockWSM.return_value.list_worktrees.return_value = [meta]
                MockWSM.return_value.get_worktree_meta.return_value = meta
                result = _resolve(("feature-x", "my_repo"))
        assert result == str(checkout)

    def test_error_on_unknown_repo_arg(self, project_root: Path):
        meta = _make_meta("feature-x", repo="my_repo")
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM:
                MockWSM.return_value.list_worktrees.return_value = [meta]
                MockWSM.return_value.get_worktree_meta.return_value = meta
                with pytest.raises(CWMError, match="not found"):
                    _resolve(("feature-x", "wrong_repo"))


# ---------------------------------------------------------------------------
# _resolve: repo name in active worktree
# ---------------------------------------------------------------------------


class TestResolveActiveRepo:
    def test_returns_repo_checkout_in_active_worktree(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        ws = str(project_root / "worktrees" / "feat_ws")
        monkeypatch.setenv("CWM_WORKSPACE", ws)
        monkeypatch.setenv("CWM_WORKTREE", "feat")

        meta = _make_meta("feat", repo="my_repo")
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM:
                MockWSM.return_value.list_worktrees.return_value = [meta]
                MockWSM.return_value.get_worktree_meta.return_value = meta
                result = _resolve(("my_repo",))
        assert result == str(project_root / "worktrees" / "feat_ws" / "src" / "my_repo")


# ---------------------------------------------------------------------------
# _resolve: unknown target
# ---------------------------------------------------------------------------


class TestResolveUnknown:
    def test_error_on_unknown_target(self, project_root: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("CWM_WORKSPACE", raising=False)
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM:
                MockWSM.return_value.list_worktrees.return_value = []
                with pytest.raises(CWMError, match="No worktree or repository found"):
                    _resolve(("nonexistent",))


# ---------------------------------------------------------------------------
# cd_resolve CLI command
# ---------------------------------------------------------------------------


class TestCdResolveCommand:
    def test_outputs_path_on_success(self, project_root: Path):
        with (
            patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root),
            patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM,
        ):
            MockWSM.return_value.list_worktrees.return_value = []
            runner = CliRunner()
            result = runner.invoke(cd_resolve, ["base"])
        assert result.exit_code == 0
        assert result.output.strip() == str(project_root)

    def test_outputs_error_on_failure(self, project_root: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("CWM_WORKSPACE", raising=False)
        with (
            patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root),
            patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM,
        ):
            MockWSM.return_value.list_worktrees.return_value = []
            runner = CliRunner()
            result = runner.invoke(cd_resolve, ["nonexistent"])
        assert result.exit_code == 1
        assert "No worktree or repository found" in result.output


# ---------------------------------------------------------------------------
# _resolve: auto_subrepo flag
# ---------------------------------------------------------------------------


class TestResolveAutoSubrepo:
    def test_no_args_goes_to_repo_checkout_when_it_exists(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        ws = str(project_root / "worktrees" / "feat_ws")
        monkeypatch.setenv("CWM_WORKSPACE", ws)
        monkeypatch.setenv("CWM_WORKTREE", "feat")

        checkout = project_root / "worktrees" / "feat_ws" / "src" / "my_repo"
        checkout.mkdir(parents=True)

        meta = _make_meta("feat", repo="my_repo")
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM:
                MockWSM.return_value.list_worktrees.return_value = [meta]
                MockWSM.return_value.get_worktree_meta.return_value = meta
                result = _resolve((), auto_subrepo=True)
        assert result == str(checkout)

    def test_no_args_returns_workspace_when_checkout_missing(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        ws = str(project_root / "worktrees" / "feat_ws")
        monkeypatch.setenv("CWM_WORKSPACE", ws)
        monkeypatch.setenv("CWM_WORKTREE", "feat")

        meta = _make_meta("feat", repo="my_repo")
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM:
                MockWSM.return_value.list_worktrees.return_value = [meta]
                MockWSM.return_value.get_worktree_meta.return_value = meta
                result = _resolve((), auto_subrepo=True)
        assert result == ws

    def test_branch_arg_goes_to_repo_checkout_when_exists(self, project_root: Path):
        checkout = project_root / "worktrees" / "feature-x_ws" / "src" / "my_repo"
        checkout.mkdir(parents=True)

        meta = _make_meta("feature-x", repo="my_repo")
        with patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root):
            with patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM:
                MockWSM.return_value.list_worktrees.return_value = [meta]
                MockWSM.return_value.get_worktree_meta.return_value = meta
                result = _resolve(("feature-x",), auto_subrepo=True)
        assert result == str(checkout)


# ---------------------------------------------------------------------------
# cd_resolve --auto-subrepo flag
# ---------------------------------------------------------------------------


class TestCdResolveAutoSubrepo:
    def test_auto_subrepo_flag_selects_repo_checkout(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        ws = str(project_root / "worktrees" / "feat_ws")
        monkeypatch.setenv("CWM_WORKSPACE", ws)
        monkeypatch.setenv("CWM_WORKTREE", "feat")

        checkout = project_root / "worktrees" / "feat_ws" / "src" / "my_repo"
        checkout.mkdir(parents=True)

        meta = _make_meta("feat", repo="my_repo")
        with (
            patch("cwm.cli.cd_cmd.find_project_root", return_value=project_root),
            patch("cwm.cli.cd_cmd.WorktreeStateManager") as MockWSM,
        ):
            MockWSM.return_value.list_worktrees.return_value = [meta]
            MockWSM.return_value.get_worktree_meta.return_value = meta
            runner = CliRunner()
            result = runner.invoke(cd_resolve, ["--auto-subrepo"])
        assert result.exit_code == 0
        assert result.output.strip() == str(checkout)


# ---------------------------------------------------------------------------
# cd CLI command (visible, shows TTY hint)
# ---------------------------------------------------------------------------


class TestCdCommand:
    def test_shows_tty_hint(self):
        runner = CliRunner()
        result = runner.invoke(cd, ["base"])
        assert result.exit_code != 0
        assert "shell integration" in result.output


# ---------------------------------------------------------------------------
# switch CLI command (visible, shows TTY hint)
# ---------------------------------------------------------------------------


class TestSwitchCommand:
    def test_shows_tty_hint(self):
        runner = CliRunner()
        result = runner.invoke(switch, ["feature-x"])
        assert result.exit_code != 0
        assert "shell integration" in result.output


# ---------------------------------------------------------------------------
# deactivate CLI command (visible, shows shell-integration hint)
# ---------------------------------------------------------------------------


class TestDeactivateCommand:
    def test_shows_shell_integration_hint(self):
        from cwm.cli.activate_cmd import deactivate

        runner = CliRunner()
        result = runner.invoke(deactivate, [])
        assert result.exit_code != 0
        assert "shell integration" in result.output
