"""Unit tests for cwm repo {show, add, remove, switch} commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from cwm.cli.main import cli
from cwm.core.config import Config
from tests.conftest import make_git_repo


class TestRepoShow:
    def test_shows_current_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        underlay = tmp_path / "ros"
        underlay.mkdir()
        project = tmp_path / "ws"
        project.mkdir()
        make_git_repo(project / "src" / "my_repo")
        monkeypatch.chdir(project)

        runner = CliRunner()
        runner.invoke(cli, ["init", "--underlay", str(underlay)], catch_exceptions=False)
        result = runner.invoke(cli, ["repo", "show"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert "my_repo" in result.output

    def test_shows_no_repo_when_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        underlay = tmp_path / "ros"
        underlay.mkdir()
        project = tmp_path / "ws"
        project.mkdir()
        monkeypatch.chdir(project)

        runner = CliRunner()
        runner.invoke(cli, ["init", "--underlay", str(underlay)], catch_exceptions=False)
        result = runner.invoke(cli, ["repo", "show"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "No repository" in result.output


class TestRepoSwitch:
    def test_switch_updates_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        underlay = tmp_path / "ros"
        underlay.mkdir()
        project = tmp_path / "ws"
        project.mkdir()
        make_git_repo(project / "src" / "repo_a")
        make_git_repo(project / "src" / "repo_b")
        monkeypatch.chdir(project)

        runner = CliRunner()
        runner.invoke(
            cli,
            ["init", "--underlay", str(underlay), "--repo", "repo_a"],
            catch_exceptions=False,
        )
        result = runner.invoke(cli, ["repo", "switch", "repo_b"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        config = Config.load(project)
        assert config.repos == ["repo_b"]

    def test_switch_validates_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        underlay = tmp_path / "ros"
        underlay.mkdir()
        project = tmp_path / "ws"
        project.mkdir()
        make_git_repo(project / "src" / "my_repo")
        monkeypatch.chdir(project)

        runner = CliRunner()
        runner.invoke(cli, ["init", "--underlay", str(underlay)], catch_exceptions=False)
        result = runner.invoke(cli, ["repo", "switch", "nonexistent_repo"])

        assert result.exit_code != 0
        assert "nonexistent_repo" in result.output


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *repos: str) -> tuple[Path, CliRunner]:
    underlay = tmp_path / "ros"
    underlay.mkdir()
    project = tmp_path / "ws"
    project.mkdir()
    for rel in repos:
        make_git_repo(project / "src" / rel)
    monkeypatch.chdir(project)
    runner = CliRunner()
    args = ["init", "--underlay", str(underlay)]
    if repos:
        args += ["--repo", repos[0]]
    runner.invoke(cli, args, catch_exceptions=False)
    return project, runner


class TestRepoAddRemove:
    def test_add_appends_to_default_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, runner = _init(tmp_path, monkeypatch, "repo_a", "group/repo_b")
        result = runner.invoke(cli, ["repo", "add", "group/repo_b"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert Config.load(project).repos == ["repo_a", "group/repo_b"]

    def test_add_is_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, runner = _init(tmp_path, monkeypatch, "repo_a")
        result = runner.invoke(cli, ["repo", "add", "repo_a"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Already" in result.output
        assert Config.load(project).repos == ["repo_a"]

    def test_add_rejects_basename_collision(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, runner = _init(tmp_path, monkeypatch, "a/dup", "b/dup")
        result = runner.invoke(cli, ["repo", "add", "b/dup"])
        assert result.exit_code != 0
        assert "basename" in result.output
        assert Config.load(project).repos == ["a/dup"]

    def test_add_validates_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _project, runner = _init(tmp_path, monkeypatch, "repo_a")
        result = runner.invoke(cli, ["repo", "add", "nope"])
        assert result.exit_code != 0
        assert "nope" in result.output

    def test_remove_drops_from_default_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, runner = _init(tmp_path, monkeypatch, "repo_a", "repo_b")
        runner.invoke(cli, ["repo", "add", "repo_b"], catch_exceptions=False)
        result = runner.invoke(cli, ["repo", "remove", "repo_a"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert Config.load(project).repos == ["repo_b"]

    def test_remove_unknown_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _project, runner = _init(tmp_path, monkeypatch, "repo_a")
        result = runner.invoke(cli, ["repo", "remove", "repo_x"])
        assert result.exit_code != 0
        assert "not in the default set" in result.output

    def test_show_lists_every_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _project, runner = _init(tmp_path, monkeypatch, "repo_a", "repo_b")
        runner.invoke(cli, ["repo", "add", "repo_b"], catch_exceptions=False)
        result = runner.invoke(cli, ["repo", "show"], catch_exceptions=False)
        assert "repo_a" in result.output
        assert "repo_b" in result.output
