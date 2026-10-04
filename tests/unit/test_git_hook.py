"""Tests for the 'cwm worktree __git_hook' interceptor."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from cwm.cli.main import cli
from cwm.cli.worktree_cmd import _parse_git_worktree_add, _strip_git_globals
from cwm.core.config import Config
from cwm.core.worktree_state import WorktreeMeta, WorktreeStateManager
from tests.conftest import make_git_repo


# ---------------------------------------------------------------------------
# _parse_git_worktree_add
# ---------------------------------------------------------------------------


class TestParseGitWorktreeAdd:
    def test_b_flag_branch_path(self) -> None:
        parsed = _parse_git_worktree_add(["-b", "feature-x", "../feature-x"])
        assert parsed is not None
        path, branch, ignored = parsed
        assert path == Path("../feature-x")
        assert branch == "feature-x"
        assert ignored == []

    def test_path_then_branch_positional(self) -> None:
        parsed = _parse_git_worktree_add(["../feature-x", "feature-x"])
        assert parsed is not None
        path, branch, _ = parsed
        assert path == Path("../feature-x")
        assert branch == "feature-x"

    def test_path_only_infers_branch_from_basename(self) -> None:
        parsed = _parse_git_worktree_add(["../feature-x"])
        assert parsed is not None
        path, branch, _ = parsed
        assert path == Path("../feature-x")
        assert branch == "feature-x"

    def test_capital_b_force_reset(self) -> None:
        parsed = _parse_git_worktree_add(["-B", "feature-y", "/tmp/feature-y"])
        assert parsed is not None
        path, branch, _ = parsed
        assert path == Path("/tmp/feature-y")
        assert branch == "feature-y"

    def test_detach_flag_reports_ignored(self) -> None:
        parsed = _parse_git_worktree_add(["--detach", "../detached"])
        assert parsed is not None
        path, branch, ignored = parsed
        assert path == Path("../detached")
        assert branch == "detached"
        assert "--detach" in ignored

    def test_force_flag_reports_ignored(self) -> None:
        parsed = _parse_git_worktree_add(["-f", "-b", "feat", "../feat"])
        assert parsed is not None
        _, _, ignored = parsed
        assert "--force" in ignored

    def test_no_args_returns_none(self) -> None:
        assert _parse_git_worktree_add([]) is None

    def test_unknown_flag_returns_none(self) -> None:
        assert _parse_git_worktree_add(["--bogus-flag", "../foo"]) is None


# ---------------------------------------------------------------------------
# Integration fixtures for the hook subcommand
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Config:
    """Minimal CWM project with one tracked repo."""
    root = tmp_path / "project"
    root.mkdir()
    config = Config(
        underlay="/opt/ros/jazzy",
        repos=["my_repo"],
        project_root=root,
    )
    for d in [
        config.cwm_dir / "worktrees",
        config.cwm_dir / "cache",
        config.worktrees_path,
    ]:
        d.mkdir(parents=True)
    make_git_repo(config.base_src_path / "my_repo")
    config.save()
    return config


def _invoke_hook(project: Config, args: list[str], monkeypatch) -> object:
    monkeypatch.chdir(project.project_root)
    runner = CliRunner()
    return runner.invoke(cli, ["worktree", "__git_hook", *args], catch_exceptions=False)


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


class TestHookAdd:
    def test_creates_workspace_and_symlink(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        link = tmp_path / "feature-x"
        result = _invoke_hook(project, ["add", "-b", "feature-x", str(link)], monkeypatch)

        assert result.exit_code == 0, (result.stdout, result.stderr)
        # Workspace exists
        ws = project.worktree_ws_path("feature-x")
        assert ws.is_dir()
        # Symlink created and points to workspace
        assert link.is_symlink()
        assert link.resolve() == ws.resolve()
        # Meta records the symlink
        meta = WorktreeMeta.load(project.worktree_meta_path("feature-x"))
        assert str(link.absolute()) in meta.agent_symlinks

    def test_emits_translation_message_to_stderr(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        link = tmp_path / "feature-y"
        result = _invoke_hook(project, ["add", "-b", "feature-y", str(link)], monkeypatch)

        assert "[CWM Agent Hook]" in result.stderr
        assert "cwm activate feature-y" in result.stderr

    def test_branch_inferred_from_path_basename(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        link = tmp_path / "feature-z"
        result = _invoke_hook(project, ["add", str(link)], monkeypatch)

        assert result.exit_code == 0
        assert project.worktree_ws_path("feature-z").is_dir()

    def test_unparseable_args_exit_1(self, project: Config, monkeypatch) -> None:
        # No positional path supplied
        result = _invoke_hook(project, ["add", "-b", "feature-q"], monkeypatch)
        assert result.exit_code == 1
        assert "Could not parse" in result.stderr or "Use 'cwm worktree" in result.stderr


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestHookList:
    def test_lists_base_repo_only_when_no_worktrees(self, project: Config, monkeypatch) -> None:
        result = _invoke_hook(project, ["list"], monkeypatch)
        assert result.exit_code == 0
        assert "my_repo" in result.stdout

    def test_lists_worktree_using_agent_symlink(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        link = tmp_path / "feature-x"
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-x")
        manager.register_agent_symlink("feature-x", link)

        result = _invoke_hook(project, ["list"], monkeypatch)
        assert result.exit_code == 0
        assert str(link.absolute()) in result.stdout
        assert "[feature-x]" in result.stdout

    def test_porcelain_format(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        link = tmp_path / "feature-x"
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-x")
        manager.register_agent_symlink("feature-x", link)

        result = _invoke_hook(project, ["list", "--porcelain"], monkeypatch)
        assert result.exit_code == 0
        assert "worktree " in result.stdout
        assert "branch refs/heads/feature-x" in result.stdout


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestHookRemove:
    def test_remove_by_symlink_path(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        link = tmp_path / "feature-x"
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-x")
        manager.register_agent_symlink("feature-x", link)

        result = _invoke_hook(project, ["remove", str(link)], monkeypatch)
        assert result.exit_code == 0, (result.stdout, result.stderr)
        assert not project.worktree_ws_path("feature-x").exists()
        assert not link.is_symlink()

    def test_remove_by_workspace_path(self, project: Config, monkeypatch) -> None:
        manager = WorktreeStateManager(project)
        ws_path = manager.create_worktree("feature-x")

        result = _invoke_hook(project, ["remove", str(ws_path)], monkeypatch)
        assert result.exit_code == 0
        assert not project.worktree_ws_path("feature-x").exists()

    def test_remove_unknown_path_returns_error(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        result = _invoke_hook(project, ["remove", str(tmp_path / "nope")], monkeypatch)
        assert result.exit_code == 1
        assert "No CWM worktree" in result.stderr


# ---------------------------------------------------------------------------
# prune / unsupported
# ---------------------------------------------------------------------------


class TestHookPrune:
    def test_prune_runs_without_stale(self, project: Config, monkeypatch) -> None:
        result = _invoke_hook(project, ["prune"], monkeypatch)
        assert result.exit_code == 0
        assert "Pruned 0" in result.stderr

    def test_prune_removes_stale(self, project: Config, monkeypatch) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-x")
        import shutil
        shutil.rmtree(project.worktree_ws_path("feature-x"))

        result = _invoke_hook(project, ["prune"], monkeypatch)
        assert result.exit_code == 0
        assert "Pruned 1" in result.stderr


class TestHookUnsupported:
    @pytest.mark.parametrize("subcmd", ["lock", "unlock", "move", "repair"])
    def test_unsupported_subcommand_exits_1(self, project: Config, subcmd: str, monkeypatch) -> None:
        result = _invoke_hook(project, [subcmd], monkeypatch)
        assert result.exit_code == 1
        assert "not supported under CWM" in result.stderr

    def test_no_subcommand_exits_1(self, project: Config, monkeypatch) -> None:
        result = _invoke_hook(project, [], monkeypatch)
        assert result.exit_code == 1
        assert "not supported" in result.stderr


# ---------------------------------------------------------------------------
# leading git global options
# ---------------------------------------------------------------------------


class TestStripGitGlobals:
    def test_no_globals_passthrough(self) -> None:
        assert _strip_git_globals(["worktree", "add"]) == (["worktree", "add"], False)

    def test_value_taking_option_skips_pair(self) -> None:
        # The path after -C must not be mistaken for the subcommand.
        assert _strip_git_globals(["-C", "/x", "worktree", "list"]) == (
            ["worktree", "list"],
            True,
        )

    def test_config_option_is_not_retargeting(self) -> None:
        rest, retargeting = _strip_git_globals(["-c", "foo=bar", "worktree", "list"])
        assert rest == ["worktree", "list"]
        assert retargeting is False

    def test_equals_form_git_dir_is_retargeting(self) -> None:
        rest, retargeting = _strip_git_globals(["--git-dir=/x", "worktree", "list"])
        assert rest == ["worktree", "list"]
        assert retargeting is True

    def test_bare_flag_skips_one(self) -> None:
        rest, retargeting = _strip_git_globals(["--no-pager", "worktree", "list"])
        assert rest == ["worktree", "list"]
        assert retargeting is False


class TestHookGlobalOptions:
    def test_retargeting_option_before_worktree_is_refused(
        self, project: Config, tmp_path: Path, monkeypatch
    ) -> None:
        link = tmp_path / "feature-x"
        result = _invoke_hook(
            project, ["-C", "/elsewhere", "worktree", "add", "-b", "feature-x", str(link)], monkeypatch
        )
        assert result.exit_code == 1
        assert "not supported under CWM" in result.stderr
        # The refused operation must not have created anything.
        assert not project.worktree_ws_path("feature-x").exists()
        assert not link.exists()

    def test_git_dir_equals_form_is_refused(self, project: Config, monkeypatch) -> None:
        result = _invoke_hook(project, ["--git-dir=/x", "worktree", "list"], monkeypatch)
        assert result.exit_code == 1
        assert "not supported under CWM" in result.stderr

    def test_benign_global_option_is_processed(self, project: Config, monkeypatch) -> None:
        # '-c foo=bar' does not retarget, so 'worktree list' runs normally.
        result = _invoke_hook(project, ["-c", "foo=bar", "worktree", "list"], monkeypatch)
        assert result.exit_code == 0
        assert "my_repo" in result.stdout


class TestHookAddErrorHandling:
    def test_existing_directory_rejected_and_worktree_rolled_back(
        self, project: Config, tmp_path: Path, monkeypatch
    ) -> None:
        """Pre-existing non-symlink path must abort the add and clean up the
        partially created worktree so retries are unblocked."""
        existing = tmp_path / "feature-x"
        existing.mkdir()

        result = _invoke_hook(project, ["add", "-b", "feature-x", str(existing)], monkeypatch)
        assert result.exit_code == 1
        # No leftover workspace
        assert not project.worktree_ws_path("feature-x").exists()
        # Real dir untouched
        assert existing.is_dir()
        assert not existing.is_symlink()
        # Helpful message
        assert "already exists" in result.stderr or "refusing" in result.stderr

    def test_ignored_flag_emits_warning(
        self, project: Config, tmp_path: Path, monkeypatch
    ) -> None:
        link = tmp_path / "feature-detached"
        result = _invoke_hook(
            project, ["add", "--detach", "-b", "feature-detached", str(link)], monkeypatch
        )
        assert result.exit_code == 0
        assert "--detach" in result.stderr and "ignored" in result.stderr


class TestHookListMergesGitWorktrees:
    def test_includes_non_cwm_worktrees(
        self, project: Config, tmp_path: Path, monkeypatch
    ) -> None:
        """Worktrees created via raw 'git worktree add' (outside CWM) must appear."""
        from cwm.util import git as gitutil
        base_repo = project.base_src_path / "my_repo"
        external = tmp_path / "external-feat"
        gitutil.worktree_add(external, "external-feat", create_branch=True, cwd=base_repo)

        result = _invoke_hook(project, ["list"], monkeypatch)
        assert result.exit_code == 0
        assert "external-feat" in result.stdout


class TestHookRemovePathNormalisation:
    def test_remove_from_different_cwd(
        self, project: Config, tmp_path: Path, monkeypatch
    ) -> None:
        """A symlink registered from one cwd must be removable from another
        cwd using a path that contains '..' (regression for Path.absolute()
        not collapsing '..' segments)."""
        from cwm.core.worktree_state import WorktreeStateManager
        link = tmp_path / "feature-x"
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-x")
        manager.register_agent_symlink("feature-x", link)

        # Invoke from a sibling directory using ../feature-x.  Set
        # CWM_PROJECT_ROOT so the hook can locate the project from cwd that
        # is not under the project tree.
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)
        monkeypatch.setenv("CWM_PROJECT_ROOT", str(project.project_root))
        runner = CliRunner()
        result = runner.invoke(
            cli, ["worktree", "__git_hook", "remove", "../feature-x"], catch_exceptions=False
        )
        assert result.exit_code == 0, (result.stdout, result.stderr)
        assert not link.is_symlink()
