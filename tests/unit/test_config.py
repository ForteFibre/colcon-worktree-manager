"""Unit tests for CWM configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cwm.core.config import Config
from cwm.errors import ConfigVersionError


class TestConfigSerialization:
    def test_roundtrip(self, tmp_path: Path) -> None:
        config = Config(
            underlay="/opt/ros/jazzy",
            worktrees_dir="worktrees",
            repos=["autoware.universe", "core/autoware_core"],
            project_root=tmp_path,
        )
        config.save()
        loaded = Config.load(tmp_path)
        assert loaded.underlay == config.underlay
        assert loaded.symlink_install == config.symlink_install
        assert loaded.worktrees_dir == config.worktrees_dir
        assert loaded.repos == ["autoware.universe", "core/autoware_core"]

    def test_roundtrip_without_repo(self, tmp_path: Path) -> None:
        config = Config(underlay="/opt/ros/jazzy", repos=[], project_root=tmp_path)
        config.save()
        loaded = Config.load(tmp_path)
        assert loaded.repos == []

    def test_v3_single_repo_config_is_migrated(self, tmp_path: Path) -> None:
        import yaml

        (tmp_path / ".cwm").mkdir()
        (tmp_path / ".cwm" / "config.yaml").write_text(
            yaml.safe_dump({
                "version": 3,
                "underlay": "/opt/ros/jazzy",
                "worktrees_dir": "worktrees",
                "symlink_install": False,
                "repo": "autoware.universe",
            })
        )
        loaded = Config.load(tmp_path)
        assert loaded.repos == ["autoware.universe"]
        assert loaded.symlink_install is False
        assert loaded.version == 4

        # Saving writes the v4 schema without the legacy key.
        loaded.save()
        data = yaml.safe_load((tmp_path / ".cwm" / "config.yaml").read_text())
        assert data["version"] == 4
        assert data["repos"] == ["autoware.universe"]
        assert "repo" not in data

    def test_v3_config_without_repo_migrates_to_empty_set(self, tmp_path: Path) -> None:
        import yaml

        (tmp_path / ".cwm").mkdir()
        (tmp_path / ".cwm" / "config.yaml").write_text(
            yaml.safe_dump({"version": 3, "underlay": "/opt/ros/jazzy"})
        )
        assert Config.load(tmp_path).repos == []

    def test_derived_paths(self, tmp_path: Path) -> None:
        config = Config(project_root=tmp_path)
        assert config.base_src_path == tmp_path / "src"
        assert config.base_install_path == tmp_path / "install"
        assert config.worktrees_path == tmp_path / "worktrees"

    def test_worktree_ws_path_sanitises_slashes(self, tmp_path: Path) -> None:
        config = Config(project_root=tmp_path)
        ws = config.worktree_ws_path("feature/perception")
        assert "feature-perception_ws" in ws.name

    def test_old_config_raises_helpful_error(self, tmp_path: Path) -> None:
        import yaml

        (tmp_path / ".cwm").mkdir()
        (tmp_path / ".cwm" / "config.yaml").write_text(
            yaml.safe_dump({"version": 1, "underlay": "/opt/ros/jazzy"})
        )
        with pytest.raises(ConfigVersionError, match="re-initialise"):
            Config.load(tmp_path)

    def test_v2_config_raises_helpful_error(self, tmp_path: Path) -> None:
        import yaml

        (tmp_path / ".cwm").mkdir()
        (tmp_path / ".cwm" / "config.yaml").write_text(
            yaml.safe_dump({"version": 2, "underlay": "/opt/ros/jazzy", "worktrees_dir": "worktrees"})
        )
        with pytest.raises(ConfigVersionError, match="re-initialise"):
            Config.load(tmp_path)


class TestConfigDefaults:
    def test_default_underlay(self) -> None:
        config = Config()
        assert config.underlay == ""

    def test_no_legacy_keys_in_config(self, tmp_path: Path) -> None:
        import yaml

        config = Config(project_root=tmp_path)
        config.save()
        data = yaml.safe_load((tmp_path / ".cwm" / "config.yaml").read_text())
        assert "mode" not in data
        assert "base_ws" not in data
        assert "sub_repos" not in data
        assert "symlink_install" in data
        assert "repo" not in data
        assert data["version"] == 4

    def test_repo_paths_property(self, tmp_path: Path) -> None:
        config = Config(project_root=tmp_path, repos=["autoware.universe", "core/autoware_core"])
        assert config.repo_paths == {
            "autoware.universe": tmp_path / "src" / "autoware.universe",
            "core/autoware_core": tmp_path / "src" / "core" / "autoware_core",
        }

    def test_repo_paths_empty_when_no_repo(self, tmp_path: Path) -> None:
        config = Config(project_root=tmp_path, repos=[])
        assert config.repo_paths == {}

    def test_worktree_checkout_path_uses_basename(self, tmp_path: Path) -> None:
        config = Config(project_root=tmp_path)
        assert config.worktree_checkout_path("feat/x", "core/autoware_core") == (
            tmp_path / "worktrees" / "feat-x_ws" / "src" / "autoware_core"
        )
