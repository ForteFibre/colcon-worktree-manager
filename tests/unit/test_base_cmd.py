"""Tests for cwm base build / clean / status / doctor commands."""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from cwm.cli.main import cli


@contextlib.contextmanager
def _patched_config(tmp_path: Path, *, underlay: Path | None = None, repos: tuple[str, ...] = ("my_repo",)):
    """Patch find_project_root + Config.load for base commands.

    *underlay* defaults to a directory containing a setup.bash so sourced builds
    pass the existence check.
    """
    if underlay is None:
        underlay = tmp_path / "ros"
        underlay.mkdir(parents=True, exist_ok=True)
        (underlay / "setup.bash").write_text("")

    with patch("cwm.cli.base_cmd.find_project_root", return_value=tmp_path), \
         patch("cwm.core.config.Config.load") as mock_cfg:
        cfg = mock_cfg.return_value
        cfg.project_root = tmp_path
        cfg.underlay = str(underlay)
        cfg.symlink_install = True
        cfg.repos = list(repos)
        cfg.repo_paths = {r: tmp_path / "src" / r for r in repos}
        cfg.base_install_path = tmp_path / "install"
        yield cfg


class TestBaseBuild:
    def test_sources_underlay_and_runs_colcon_build(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched_config(tmp_path), \
             patch("cwm.util.colcon_runner.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = runner.invoke(cli, ["base", "build"])

        assert result.exit_code == 0, result.output
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["bash", "-c"]
        assert "source" in cmd[2]
        assert "colcon build" in cmd[2]
        assert "--symlink-install" in cmd[2]
        assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_forwards_extra_colcon_args(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched_config(tmp_path), \
             patch("cwm.util.colcon_runner.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = runner.invoke(cli, ["base", "build", "--", "--continue-on-error"])

        assert result.exit_code == 0, result.output
        assert "--continue-on-error" in mock_run.call_args[0][0][2]

    def test_missing_underlay_errors(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope"
        runner = CliRunner()
        with _patched_config(tmp_path, underlay=missing), \
             patch("cwm.util.colcon_runner.subprocess.run") as mock_run:
            result = runner.invoke(cli, ["base", "build"])

        assert result.exit_code != 0
        assert "Underlay setup not found" in result.output
        mock_run.assert_not_called()


class TestBaseUpdate:
    def test_pulls_then_sourced_build(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "my_repo").mkdir(parents=True)
        runner = CliRunner()
        with _patched_config(tmp_path), \
             patch("cwm.cli.base_cmd.git.pull") as mock_pull, \
             patch("cwm.util.colcon_runner.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = runner.invoke(cli, ["base", "update"])

        assert result.exit_code == 0, result.output
        mock_pull.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["bash", "-c"]
        assert "colcon build" in cmd[2]

    def test_pulls_every_repo_in_default_set(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "repo_a").mkdir(parents=True)
        (tmp_path / "src" / "repo_b").mkdir(parents=True)
        runner = CliRunner()
        with _patched_config(tmp_path, repos=("repo_a", "repo_b")), \
             patch("cwm.cli.base_cmd.git.pull") as mock_pull, \
             patch("cwm.util.colcon_runner.subprocess.run") as mock_run:
            result = runner.invoke(cli, ["base", "update", "--no-build"])

        assert result.exit_code == 0, result.output
        assert [c.kwargs["cwd"] for c in mock_pull.call_args_list] == [
            tmp_path / "src" / "repo_a",
            tmp_path / "src" / "repo_b",
        ]
        assert "repo_a: pulled" in result.output
        assert "repo_b: pulled" in result.output
        mock_run.assert_not_called()

    def test_failed_pull_is_reported_per_repo_and_skips_build(self, tmp_path: Path) -> None:
        from cwm.errors import GitError

        (tmp_path / "src" / "repo_a").mkdir(parents=True)
        (tmp_path / "src" / "repo_b").mkdir(parents=True)

        def fake_pull(*, cwd: Path) -> None:
            if cwd.name == "repo_a":
                raise GitError("diverged")

        runner = CliRunner()
        with _patched_config(tmp_path, repos=("repo_a", "repo_b")), \
             patch("cwm.cli.base_cmd.git.pull", side_effect=fake_pull) as mock_pull, \
             patch("cwm.util.colcon_runner.subprocess.run") as mock_run:
            result = runner.invoke(cli, ["base", "update"])

        assert result.exit_code != 0
        # repo_b is still pulled after repo_a fails.
        assert mock_pull.call_count == 2
        assert "repo_a: pull failed" in result.output
        assert "repo_b: pulled" in result.output
        assert "Pull failed for: repo_a" in result.output
        mock_run.assert_not_called()

    def test_no_repos_errors(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched_config(tmp_path, repos=()):
            result = runner.invoke(cli, ["base", "update"])
        assert result.exit_code != 0
        assert "cwm repo add" in result.output

    def test_no_build_skips_colcon(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "my_repo").mkdir(parents=True)
        runner = CliRunner()
        with _patched_config(tmp_path), \
             patch("cwm.cli.base_cmd.git.pull"), \
             patch("cwm.util.colcon_runner.subprocess.run") as mock_run:
            result = runner.invoke(cli, ["base", "update", "--no-build"])

        assert result.exit_code == 0, result.output
        mock_run.assert_not_called()


class TestBaseClean:
    def _make_artifacts(self, root: Path) -> list[Path]:
        dirs = [root / "build", root / "install", root / "log"]
        for d in dirs:
            d.mkdir(parents=True)
            (d / "marker").write_text("")
        return dirs

    def test_yes_removes_artifacts(self, tmp_path: Path) -> None:
        dirs = self._make_artifacts(tmp_path)
        runner = CliRunner()
        with _patched_config(tmp_path):
            result = runner.invoke(cli, ["base", "clean", "--yes"])

        assert result.exit_code == 0, result.output
        assert all(not d.exists() for d in dirs)

    def test_decline_keeps_artifacts(self, tmp_path: Path) -> None:
        dirs = self._make_artifacts(tmp_path)
        runner = CliRunner()
        with _patched_config(tmp_path):
            result = runner.invoke(cli, ["base", "clean"], input="n\n")

        assert result.exit_code == 0, result.output
        assert all(d.exists() for d in dirs)
        assert "Aborted" in result.output

    def test_nothing_to_clean(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched_config(tmp_path):
            result = runner.invoke(cli, ["base", "clean", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Nothing to clean" in result.output


class TestWsCleanBaseDeprecation:
    def test_ws_clean_base_warns_but_works(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        runner = CliRunner()
        with patch("cwm.cli.clean_cmd.find_project_root", return_value=tmp_path), \
             patch("cwm.core.config.Config.load") as mock_cfg:
            cfg = mock_cfg.return_value
            cfg.project_root = tmp_path
            cfg.worktrees_path = tmp_path / "worktrees"
            cfg.worktrees_path.mkdir()
            result = runner.invoke(cli, ["ws", "clean", "--all", "--base"])

        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output
        assert not (tmp_path / "build").exists()


class TestBaseStatus:
    def test_json_reports_built_state(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched_config(tmp_path), \
             patch("cwm.cli.base_cmd._collect_base", return_value={"built": True, "dirty": False, "repo": "my_repo"}):
            result = runner.invoke(cli, ["base", "status", "--json"])

        assert result.exit_code == 0, result.output
        assert '"built": true' in result.output

    def test_human_output(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched_config(tmp_path), \
             patch("cwm.cli.base_cmd._collect_base", return_value={"built": False, "dirty": True, "repo": "my_repo"}):
            result = runner.invoke(cli, ["base", "status"])

        assert result.exit_code == 0, result.output
        assert "Base workspace" in result.output
        assert "not built" in result.output


class TestBaseDoctor:
    def _make_build(self, root: Path, pkg: str, source_dir: Path | None) -> Path:
        """Create build/<pkg>/CMakeCache.txt pointing at *source_dir* (None = no cache)."""
        d = root / "build" / pkg
        d.mkdir(parents=True)
        if source_dir is not None:
            (d / "CMakeCache.txt").write_text(
                f"CMAKE_HOME_DIRECTORY:INTERNAL={source_dir}\n"
            )
        return d

    def test_detects_only_stale_dirs(self, tmp_path: Path) -> None:
        live_src = tmp_path / "src" / "live_pkg"
        live_src.mkdir(parents=True)
        live = self._make_build(tmp_path, "live_pkg", live_src)
        stale = self._make_build(tmp_path, "moved_pkg", tmp_path / "src" / "gone")
        no_cache = self._make_build(tmp_path, "py_pkg", None)

        runner = CliRunner()
        with _patched_config(tmp_path):
            result = runner.invoke(cli, ["base", "doctor"])

        assert result.exit_code == 0, result.output
        assert "moved_pkg" in result.output
        assert "live_pkg" not in result.output
        assert "py_pkg" not in result.output
        # nothing deleted without --fix
        assert live.exists() and stale.exists() and no_cache.exists()

    def test_fix_removes_only_stale(self, tmp_path: Path) -> None:
        live_src = tmp_path / "src" / "live_pkg"
        live_src.mkdir(parents=True)
        live = self._make_build(tmp_path, "live_pkg", live_src)
        stale = self._make_build(tmp_path, "moved_pkg", tmp_path / "src" / "gone")

        runner = CliRunner()
        with _patched_config(tmp_path):
            result = runner.invoke(cli, ["base", "doctor", "--fix"])

        assert result.exit_code == 0, result.output
        assert not stale.exists()
        assert live.exists()

    def test_json_output(self, tmp_path: Path) -> None:
        self._make_build(tmp_path, "moved_pkg", tmp_path / "src" / "gone")
        runner = CliRunner()
        with _patched_config(tmp_path):
            result = runner.invoke(cli, ["base", "doctor", "--json"])

        assert result.exit_code == 0, result.output
        assert '"stale_build_dirs"' in result.output
        assert "moved_pkg" in result.output

    def test_no_build_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched_config(tmp_path):
            result = runner.invoke(cli, ["base", "doctor"])

        assert result.exit_code == 0, result.output
        assert "No stale build directories" in result.output
