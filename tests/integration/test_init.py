"""Integration tests for cwm init."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

import cwm.util.ros_env as ros_env
from cwm.cli.main import cli
from cwm.core.config import COLCON_IGNORE, Config
from tests.conftest import make_git_repo


class TestCwmInit:
    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "project"
        project.mkdir()

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=project):
            result = runner.invoke(
                cli,
                ["init", "--underlay", str(underlay)],
                catch_exceptions=False,
            )
            assert result.exit_code == 0, result.output

            cwd = Path.cwd()
            assert (cwd / ".cwm").is_dir()
            assert (cwd / ".cwm" / "config.yaml").is_file()
            assert (cwd / "worktrees").is_dir()
            assert (cwd / "worktrees" / COLCON_IGNORE).is_file()
            assert not (cwd / "base_ws").exists()

    def test_creates_git_wrapper(self, tmp_path: Path) -> None:
        """cwm init must drop an executable .cwm/bin/git wrapper."""
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "project"
        project.mkdir()

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=project):
            result = runner.invoke(
                cli,
                ["init", "--underlay", str(underlay)],
                catch_exceptions=False,
            )
            assert result.exit_code == 0, result.output

            wrapper = Path.cwd() / ".cwm" / "bin" / "git"
            assert wrapper.is_file()
            assert os.access(wrapper, os.X_OK)
            assert "cwm worktree __git_hook" in wrapper.read_text()

    def test_config_persisted(self, tmp_path: Path) -> None:
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "project"
        project.mkdir()

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=project):
            result = runner.invoke(
                cli,
                ["init", "--underlay", str(underlay)],
                catch_exceptions=False,
            )
            assert result.exit_code == 0

            config = Config.load(Path.cwd())
            assert config.underlay == str(underlay)

    def test_auto_detects_underlay(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ros_base = tmp_path / "opt" / "ros"
        ros_base.mkdir(parents=True)
        jazzy = ros_base / "jazzy"
        jazzy.mkdir()
        (jazzy / "setup.bash").write_text("# fake")

        project = tmp_path / "project"
        project.mkdir()

        monkeypatch.setattr(ros_env, "ROS_INSTALL_BASE", ros_base)
        monkeypatch.delenv("ROS_DISTRO", raising=False)

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=project):
            result = runner.invoke(cli, ["init"], catch_exceptions=False)
            assert result.exit_code == 0, result.output
            assert "Auto-detected" in result.output
            assert "jazzy" in result.output

            config = Config.load(Path.cwd())
            assert config.underlay == str(jazzy)

    def test_init_fails_when_no_ros_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ros_env, "ROS_INSTALL_BASE", tmp_path / "empty")
        monkeypatch.delenv("ROS_DISTRO", raising=False)

        project = tmp_path / "project"
        project.mkdir()

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=project):
            result = runner.invoke(cli, ["init"])
            assert result.exit_code != 0
            assert "--underlay" in result.output

    def test_double_init_fails(self, tmp_path: Path) -> None:
        underlay = tmp_path / "ros"
        underlay.mkdir()

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init", "--underlay", str(underlay)])
            result = runner.invoke(cli, ["init", "--underlay", str(underlay)])
            assert result.exit_code != 0
            assert "already initialised" in result.output

    def test_adopt_existing_workspace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Existing src/ is adopted as-is without touching existing files."""
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "ws"
        project.mkdir()

        make_git_repo(project / "src" / "my_pkg", branch="develop")
        existing_file = project / "src" / "my_pkg" / "package.xml"
        existing_file.write_text("<package/>")

        monkeypatch.chdir(project)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["init", "--underlay", str(underlay)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Adopted" in result.output
        assert existing_file.exists()

    def test_adopt_single_repo_auto_selects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single repo in src/ is automatically selected as the tracked repo."""
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "ws"
        project.mkdir()
        make_git_repo(project / "src" / "my_repo")
        monkeypatch.chdir(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["init", "--underlay", str(underlay)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        config = Config.load(project)
        assert config.repos == ["my_repo"]

    def test_init_accepts_repo_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--repo flag overrides auto-detection."""
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "ws"
        project.mkdir()
        make_git_repo(project / "src" / "repo_a")
        make_git_repo(project / "src" / "repo_b")
        monkeypatch.chdir(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["init", "--underlay", str(underlay), "--repo", "repo_a"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        config = Config.load(project)
        assert config.repos == ["repo_a"]

    def test_init_accepts_multiple_repo_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--repo may be repeated to seed a multi-repo default set."""
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "ws"
        project.mkdir()
        make_git_repo(project / "src" / "repo_a")
        make_git_repo(project / "src" / "group" / "repo_b")
        monkeypatch.chdir(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["init", "--underlay", str(underlay), "--repo", "repo_a", "--repo", "group/repo_b"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert Config.load(project).repos == ["repo_a", "group/repo_b"]

    def test_init_rejects_basename_collision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "ws"
        project.mkdir()
        make_git_repo(project / "src" / "a" / "dup")
        make_git_repo(project / "src" / "b" / "dup")
        monkeypatch.chdir(project)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["init", "--underlay", str(underlay), "--repo", "a/dup", "--repo", "b/dup"]
        )
        assert result.exit_code != 0
        assert "basename" in result.output

    def test_fresh_init_leaves_repo_unset(self, tmp_path: Path) -> None:
        """Fresh init without src/ leaves repo unset."""
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "fresh_ws"
        project.mkdir()

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=project):
            result = runner.invoke(
                cli,
                ["init", "--underlay", str(underlay)],
                catch_exceptions=False,
            )
            assert result.exit_code == 0, result.output
            config = Config.load(Path.cwd())
            assert config.repos == []

    def test_fresh_init_does_not_create_src(self, tmp_path: Path) -> None:
        """Fresh init leaves src/ creation to the user."""
        underlay = tmp_path / "ros"
        underlay.mkdir()

        project = tmp_path / "fresh_ws"
        project.mkdir()

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=project):
            result = runner.invoke(
                cli,
                ["init", "--underlay", str(underlay)],
                catch_exceptions=False,
            )
            assert result.exit_code == 0, result.output
            assert not (Path.cwd() / "src").exists()
            assert "Clone your repository into src/" in result.output
