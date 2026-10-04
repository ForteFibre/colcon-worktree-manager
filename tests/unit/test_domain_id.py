"""Per-worktree ROS_DOMAIN_ID leases."""

from __future__ import annotations

import fcntl
import json
import re
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from cwm.cli.activate_cmd import generate_activate_script
from cwm.cli.main import cli
from cwm.core.config import DEFAULT_DOMAIN_ID_POOL, Config
from cwm.core.worktree_state import WorktreeMeta, WorktreeStateManager
from cwm.errors import CWMError, DomainIdPoolExhaustedError
from tests.conftest import make_git_repo


@pytest.fixture
def project(tmp_path: Path) -> Config:
    root = tmp_path / "project"
    root.mkdir()
    underlay = tmp_path / "ros"
    underlay.mkdir()
    config = Config(underlay=str(underlay), repos=["my_repo"], project_root=root)
    for d in [config.cwm_dir / "worktrees", config.cwm_dir / "cache", config.worktrees_path]:
        d.mkdir(parents=True)
    make_git_repo(config.base_src_path / "my_repo")
    config.save()
    return config


def _with_pool(config: Config, low: int, high: int) -> Config:
    config.domain_id_pool = (low, high)
    config.save()
    return config


class TestConfigPool:
    def test_default_pool(self, tmp_path: Path) -> None:
        Config(project_root=tmp_path).save()
        loaded = Config.load(tmp_path)
        assert loaded.domain_id_pool == DEFAULT_DOMAIN_ID_POOL == (215, 232)
        data = yaml.safe_load((tmp_path / ".cwm" / "config.yaml").read_text())
        assert data["domain_id_pool"] == [215, 232]

    def test_custom_pool_roundtrip(self, tmp_path: Path) -> None:
        Config(project_root=tmp_path, domain_id_pool=(50, 60)).save()
        assert Config.load(tmp_path).domain_id_pool == (50, 60)

    @pytest.mark.parametrize("pool", [[232, 215], [215], "215-232", [215, 300], [-1, 5]])
    def test_invalid_pool_rejected(self, tmp_path: Path, pool: object) -> None:
        (tmp_path / ".cwm").mkdir()
        (tmp_path / ".cwm" / "config.yaml").write_text(
            yaml.safe_dump({"version": 4, "underlay": "/opt/ros/jazzy", "domain_id_pool": pool})
        )
        with pytest.raises(CWMError, match="domain_id_pool"):
            Config.load(tmp_path)


class TestAllocation:
    def test_lowest_free_id_per_worktree(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("a")
        manager.create_worktree("b")
        assert manager.get_worktree_meta("a").ros_domain_id == 215
        assert manager.get_worktree_meta("b").ros_domain_id == 216

    def test_released_id_is_reused(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("a")
        manager.create_worktree("b")
        manager.remove_worktree("a")

        manager.create_worktree("c")
        assert manager.get_worktree_meta("c").ros_domain_id == 215

    def test_exhausted_pool_errors_without_side_effects(self, project: Config) -> None:
        manager = WorktreeStateManager(_with_pool(project, 215, 216))
        manager.create_worktree("a")
        manager.create_worktree("b")

        with pytest.raises(DomainIdPoolExhaustedError, match="215-216"):
            manager.create_worktree("c")

        assert not project.worktree_ws_path("c").exists()
        assert not project.worktree_meta_path("c").exists()
        from cwm.util import git as gitutil
        assert not gitutil.branch_exists("c", cwd=project.repo_path("my_repo"))

    def test_lazy_lease_for_legacy_worktree(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("old")
        manager.create_worktree("new")  # 216
        meta_path = project.worktree_meta_path("old")
        data = yaml.safe_load(meta_path.read_text())
        del data["ros_domain_id"]
        meta_path.write_text(yaml.safe_dump(data))
        manager.create_worktree("newer")  # takes 215, freed by 'old'

        assert WorktreeMeta.load(meta_path).ros_domain_id is None
        assert manager.ensure_domain_id("old") == 217
        assert WorktreeMeta.load(meta_path).ros_domain_id == 217
        # Stable on subsequent calls.
        assert manager.ensure_domain_id("old") == 217

    def test_lazy_lease_exhausted(self, project: Config) -> None:
        manager = WorktreeStateManager(_with_pool(project, 215, 215))
        manager.create_worktree("a")
        meta =WorktreeMeta(branch="legacy", created_at="", repos={})
        meta.save(project.worktree_meta_path("legacy"))
        with pytest.raises(DomainIdPoolExhaustedError):
            manager.ensure_domain_id("legacy")

    def test_ensure_domain_id_holds_lock(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("a")
        lock_path = project.cwm_dir / "lock"
        original = WorktreeStateManager.get_worktree_meta

        def checking(self: WorktreeStateManager, branch: str) -> WorktreeMeta:
            fd = os.open(lock_path, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            return original(self, branch)

        with patch.object(WorktreeStateManager, "get_worktree_meta", checking):
            manager.ensure_domain_id("a")


class TestActivateOutput:
    def _activate(self, project: Config, monkeypatch, branch: str) -> str:
        monkeypatch.chdir(project.project_root)
        monkeypatch.delenv("CWM_PROJECT_ROOT", raising=False)
        result = CliRunner().invoke(cli, ["activate", branch], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        return result.output

    def test_script_exports_domain_and_localhost_range(self, project: Config, monkeypatch) -> None:
        WorktreeStateManager(project).create_worktree("feat")
        script = self._activate(project, monkeypatch, "feat")
        assert "export ROS_DOMAIN_ID=215" in script
        assert "export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST" in script
        # deactivate restores both through the snapshot mechanism.
        assert "_CWM_OLD_ROS_DOMAIN_ID" in script
        assert "_CWM_WAS_UNSET_ROS_AUTOMATIC_DISCOVERY_RANGE" in script

    def test_activate_leases_lazily(self, project: Config, monkeypatch) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feat")
        meta = manager.get_worktree_meta("feat")
        meta.ros_domain_id = None
        meta.save(project.worktree_meta_path("feat"))

        script = self._activate(project, monkeypatch, "feat")
        assert "export ROS_DOMAIN_ID=215" in script
        assert manager.get_worktree_meta("feat").ros_domain_id == 215

    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
    def test_bash_deactivate_restores_previous_values(self, project: Config, monkeypatch) -> None:
        WorktreeStateManager(project).create_worktree("feat")
        script = self._activate(project, monkeypatch, "feat")
        script_path = project.project_root / "activate.bash"
        script_path.write_text(script)

        env = {k: v for k, v in os.environ.items() if not k.startswith(("CWM_", "_CWM_"))}
        env["ROS_DOMAIN_ID"] = "7"
        env.pop("ROS_AUTOMATIC_DISCOVERY_RANGE", None)
        out = subprocess.run(
            [
                "bash", "--noprofile", "--norc", "-c",
                f'source {script_path} >/dev/null; '
                'echo "IN=$ROS_DOMAIN_ID/$ROS_AUTOMATIC_DISCOVERY_RANGE"; '
                'deactivate; '
                'echo "OUT=$ROS_DOMAIN_ID/${ROS_AUTOMATIC_DISCOVERY_RANGE-unset}"',
            ],
            env=env, capture_output=True, text=True, check=True,
        ).stdout
        assert "IN=215/LOCALHOST" in out
        assert "OUT=7/unset" in out

    def test_generate_without_domain_id_has_no_export(self) -> None:
        script = generate_activate_script(
            branch="b", project_root="/p", workspace="/p/w", underlay="/u",
            base_install="/p/install", overlay_install="/p/w/install",
        )
        assert not re.search(r"^export ROS_DOMAIN_ID=\d", script, re.MULTILINE)
        assert "No ROS_DOMAIN_ID leased" in script


class TestReporting:
    def _cli(self, project: Config, monkeypatch, *args: str):
        monkeypatch.chdir(project.project_root)
        monkeypatch.delenv("CWM_PROJECT_ROOT", raising=False)
        return CliRunner().invoke(cli, list(args), catch_exceptions=False)

    def test_inspect_env_includes_domain(self, project: Config, monkeypatch) -> None:
        WorktreeStateManager(project).create_worktree("feat")
        payload = json.loads(self._cli(project, monkeypatch, "inspect", "env", "feat").output)
        assert payload["ROS_DOMAIN_ID"] == "215"
        assert payload["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"

    def test_list_and_status_show_domain(self, project: Config, monkeypatch) -> None:
        WorktreeStateManager(project).create_worktree("feat")
        listing = json.loads(self._cli(project, monkeypatch, "worktree", "list", "--json").output)
        assert listing["worktrees"][0]["ros_domain_id"] == 215
        assert "domain 215" in self._cli(project, monkeypatch, "worktree", "list").output

        status = json.loads(self._cli(project, monkeypatch, "ws", "status", "--json").output)
        assert status["worktrees"][0]["ros_domain_id"] == 215
        assert "domain 215" in self._cli(project, monkeypatch, "ws", "status").output

    def test_add_json_reports_domain(self, project: Config, monkeypatch) -> None:
        payload = json.loads(self._cli(project, monkeypatch, "worktree", "add", "feat", "--json").output)
        assert payload["ros_domain_id"] == 215
