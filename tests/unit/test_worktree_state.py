"""Unit tests for WorktreeStateManager."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cwm.core.config import COLCON_IGNORE, Config
from cwm.core.worktree_state import WorktreeMeta, WorktreeStateManager
from cwm.errors import (
    NoRepoSelectedError,
    WorktreeExistsError,
)
from tests.conftest import make_git_repo


@pytest.fixture
def project(tmp_path: Path) -> Config:
    """Minimal project with one git repo under src/."""
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


class TestCreateWorktree:
    def test_creates_workspace_dirs(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feature-fix")

        assert (ws / "build").is_dir()
        assert (ws / "install").is_dir()
        assert (ws / "log").is_dir()

    def test_creates_git_worktree(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")

        checkout = project.worktree_ws_path("feature-fix") / "src" / "my_repo"
        assert checkout.is_dir()
        assert (checkout / ".git").exists()

    def test_saves_metadata(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")

        meta = WorktreeMeta.load(project.worktree_meta_path("feature-fix"))
        assert meta.branch == "feature-fix"
        assert list(meta.repos) == ["my_repo"]
        assert meta.repos["my_repo"].base_sha != ""
        assert meta.repos["my_repo"].base_branch == "main"

    def test_raises_if_already_exists(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        with pytest.raises(WorktreeExistsError):
            manager.create_worktree("feature-fix")

    def test_raises_when_no_repo_selected(self, tmp_path: Path) -> None:
        root = tmp_path / "no_repo"
        root.mkdir()
        config = Config(underlay="/opt/ros/jazzy", repos=[], project_root=root)
        (config.cwm_dir / "worktrees").mkdir(parents=True)
        manager = WorktreeStateManager(config)
        with pytest.raises(NoRepoSelectedError):
            manager.create_worktree("feature-fix")

    def test_places_colcon_ignore_marker(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        assert (project.worktrees_path / COLCON_IGNORE).is_file()


class TestRemoveWorktree:
    def test_removes_workspace_and_meta(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        ws = project.worktree_ws_path("feature-fix")

        manager.remove_worktree("feature-fix")

        assert not ws.exists()
        assert not project.worktree_meta_path("feature-fix").exists()

    def test_idempotent_when_checkout_already_deleted(self, project: Config) -> None:
        """Core bug fix: remove must not fail when ws_path was manually deleted."""
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        ws = project.worktree_ws_path("feature-fix")

        import shutil
        shutil.rmtree(ws)

        # Must not raise WorktreeNotFoundError
        manager.remove_worktree("feature-fix")

        assert not project.worktree_meta_path("feature-fix").exists()

    def test_idempotent_when_meta_already_deleted(self, project: Config) -> None:
        """Remove must clean up git side even if meta is missing."""
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        meta_path = project.worktree_meta_path("feature-fix")
        meta_path.unlink()

        # Must not raise; git worktree remove + prune should run
        manager.remove_worktree("feature-fix")

        assert not project.worktree_ws_path("feature-fix").exists()

    def test_remove_calls_git_prune(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")

        with patch("cwm.util.git.worktree_prune") as mock_prune:
            manager.remove_worktree("feature-fix")

        mock_prune.assert_called_once()

    def test_delete_branch_flag(self, project: Config) -> None:
        from cwm.util import git as gitutil
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")

        manager.remove_worktree("feature-fix", delete_branch=True)

        assert not gitutil.branch_exists("feature-fix", cwd=project.base_src_path / "my_repo")


class TestAgentSymlinks:
    def test_metadata_round_trips_agent_symlinks(self, project: Config, tmp_path: Path) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        link = tmp_path / "feature-fix"

        manager.register_agent_symlink("feature-fix", link)

        reloaded = WorktreeMeta.load(project.worktree_meta_path("feature-fix"))
        assert str(link.absolute()) in reloaded.agent_symlinks
        assert link.is_symlink()
        assert link.resolve() == project.worktree_ws_path("feature-fix").resolve()

    def test_register_is_idempotent(self, project: Config, tmp_path: Path) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        link = tmp_path / "feature-fix"

        manager.register_agent_symlink("feature-fix", link)
        manager.register_agent_symlink("feature-fix", link)

        meta = WorktreeMeta.load(project.worktree_meta_path("feature-fix"))
        assert meta.agent_symlinks.count(str(link.absolute())) == 1

    def test_remove_worktree_unlinks_symlink(self, project: Config, tmp_path: Path) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        link = tmp_path / "feature-fix"
        manager.register_agent_symlink("feature-fix", link)

        manager.remove_worktree("feature-fix")

        assert not link.is_symlink()
        assert not link.exists()

    def test_remove_worktree_tolerates_already_deleted_symlink(self, project: Config, tmp_path: Path) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        link = tmp_path / "feature-fix"
        manager.register_agent_symlink("feature-fix", link)
        link.unlink()  # user removed the symlink manually

        # Must not raise
        manager.remove_worktree("feature-fix")


class TestLifecycleLocking:
    """Lifecycle methods must hold the .cwm/lock flock across their git +
    metadata critical section, so concurrent agents are serialized."""

    @staticmethod
    def _assert_lock_held(cwm_dir: Path) -> None:
        """Fail if an independent fd can grab .cwm/lock right now."""
        fd = os.open(cwm_dir / "lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_create_worktree_holds_lock(self, project: Config) -> None:
        manager = WorktreeStateManager(project)

        def side_effect(*args: object, **kwargs: object) -> None:
            self._assert_lock_held(project.cwm_dir)

        with patch("cwm.util.git.worktree_add", side_effect=side_effect):
            manager.create_worktree("feature-fix")

    def test_remove_worktree_holds_lock(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")

        def side_effect(*args: object, **kwargs: object) -> None:
            self._assert_lock_held(project.cwm_dir)

        with patch("cwm.util.git.worktree_remove", side_effect=side_effect):
            manager.remove_worktree("feature-fix")

    def test_prune_stale_holds_lock(self, project: Config) -> None:
        manager = WorktreeStateManager(project)

        def side_effect(*args: object, **kwargs: object) -> None:
            self._assert_lock_held(project.cwm_dir)

        with patch("cwm.util.git.worktree_prune", side_effect=side_effect):
            manager.prune_stale()

    def test_create_worktree_creates_lock_file(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        assert (project.cwm_dir / "lock").exists()


class TestInitProjectGitWrapper:
    def test_writes_executable_git_wrapper(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        WorktreeStateManager.init_project(root, underlay="/opt/ros/jazzy")

        wrapper = root / ".cwm" / "bin" / "git"
        assert wrapper.is_file()
        assert os.access(wrapper, os.X_OK)
        content = wrapper.read_text()
        assert "cwm worktree __git_hook" in content
        assert content.startswith("#!/usr/bin/env bash")

    def test_wrapper_has_recursion_sentinel_check(self, tmp_path: Path) -> None:
        """Recursion guard prevents the wrapper from re-entering itself when
        CWM's own subprocess calls go through PATH (issue: PATH wrapper
        recursion during activated shell)."""
        root = tmp_path / "project"
        root.mkdir()
        WorktreeStateManager.init_project(root, underlay="/opt/ros/jazzy")
        wrapper = root / ".cwm" / "bin" / "git"
        content = wrapper.read_text()
        assert "CWM_GIT_HOOK_DEPTH" in content

    def test_wrapper_has_project_root_scope_guard(self, tmp_path: Path) -> None:
        """Wrapper degrades to real git when CWM_PROJECT_ROOT is not set
        (= outside activated context), so a stray .cwm/bin entry in PATH does
        not hijack unrelated repositories."""
        root = tmp_path / "project"
        root.mkdir()
        WorktreeStateManager.init_project(root, underlay="/opt/ros/jazzy")
        wrapper = root / ".cwm" / "bin" / "git"
        content = wrapper.read_text()
        assert "CWM_PROJECT_ROOT" in content


class TestGitWrapperBehaviour:
    """Execute the generated .cwm/bin/git wrapper under bash to verify its
    runtime pass-through guards (the static tests above only assert content).

    This covers the subprocess-capture path that the shell function cannot: the
    wrapper must forward 'git worktree ...' to the CWM hook only inside an
    activated project, and otherwise delegate transparently to the real git.
    """

    @staticmethod
    def _prepare(tmp_path: Path) -> tuple[Path, str, Path]:
        from cwm.core.worktree_state import _write_git_wrapper

        log = tmp_path / "calls.log"
        realbin = tmp_path / "realbin"
        realbin.mkdir()
        real_git = realbin / "git"
        real_git.write_text(f'#!/usr/bin/env bash\nprintf "REAL_GIT %s\\n" "$*" >> "{log}"\n')
        real_git.chmod(0o755)

        cwmbin = tmp_path / "cwmbin"
        cwmbin.mkdir()
        cwm_stub = cwmbin / "cwm"
        cwm_stub.write_text(f'#!/usr/bin/env bash\nprintf "CWM %s\\n" "$*" >> "{log}"\n')
        cwm_stub.chmod(0o755)

        wrapper = tmp_path / "shim" / "git"
        _write_git_wrapper(wrapper)

        # Fake bins take precedence; the real PATH tail supplies bash/dirname/etc.
        path = os.pathsep.join(
            [str(wrapper.parent), str(realbin), str(cwmbin), os.environ.get("PATH", "")]
        )
        return wrapper, path, log

    @staticmethod
    def _invoke(wrapper: Path, path: str, args: list[str], **env_overrides: str) -> None:
        env = os.environ.copy()
        env["PATH"] = path
        env.pop("CWM_PROJECT_ROOT", None)
        env.pop("CWM_GIT_HOOK_DEPTH", None)
        env.update(env_overrides)
        bash = shutil.which("bash") or "/bin/bash"
        subprocess.run([bash, str(wrapper), *args], env=env, check=False)

    def test_delegates_to_real_git_when_depth_sentinel_set(self, tmp_path: Path) -> None:
        wrapper, path, log = self._prepare(tmp_path)
        self._invoke(
            wrapper,
            path,
            ["worktree", "list"],
            CWM_PROJECT_ROOT=str(tmp_path),
            CWM_GIT_HOOK_DEPTH="1",
        )
        assert log.read_text() == "REAL_GIT worktree list\n"

    def test_delegates_to_real_git_when_project_root_unset(self, tmp_path: Path) -> None:
        wrapper, path, log = self._prepare(tmp_path)
        self._invoke(wrapper, path, ["worktree", "list"])  # no CWM_PROJECT_ROOT
        assert log.read_text() == "REAL_GIT worktree list\n"

    def test_delegates_to_real_git_for_non_worktree_subcommand(self, tmp_path: Path) -> None:
        wrapper, path, log = self._prepare(tmp_path)
        self._invoke(wrapper, path, ["status"], CWM_PROJECT_ROOT=str(tmp_path))
        assert log.read_text() == "REAL_GIT status\n"

    def test_forwards_worktree_to_cwm_hook_when_activated(self, tmp_path: Path) -> None:
        wrapper, path, log = self._prepare(tmp_path)
        self._invoke(
            wrapper,
            path,
            ["worktree", "add", "-b", "x", "../x"],
            CWM_PROJECT_ROOT=str(tmp_path),
        )
        assert log.read_text() == "CWM worktree __git_hook add -b x ../x\n"

    def test_forwards_worktree_behind_global_option_to_cwm_hook(self, tmp_path: Path) -> None:
        """'git -C <path> worktree ...' must not slip past the interceptor; the
        original argv is forwarded unshifted so the hook can apply policy."""
        wrapper, path, log = self._prepare(tmp_path)
        self._invoke(
            wrapper,
            path,
            ["-C", "/x", "worktree", "add", "-b", "y", "../y"],
            CWM_PROJECT_ROOT=str(tmp_path),
        )
        assert log.read_text() == "CWM worktree __git_hook -C /x worktree add -b y ../y\n"

    def test_delegates_to_real_git_for_global_option_non_worktree(self, tmp_path: Path) -> None:
        """A global option in front of a non-worktree subcommand must still go
        to real git (the value after -C must not be mistaken for a subcommand)."""
        wrapper, path, log = self._prepare(tmp_path)
        self._invoke(wrapper, path, ["-C", "/x", "status"], CWM_PROJECT_ROOT=str(tmp_path))
        assert log.read_text() == "REAL_GIT -C /x status\n"


class TestRegisterAgentSymlinkRejection:
    def test_refuses_existing_regular_directory(self, project: Config, tmp_path: Path) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        existing = tmp_path / "feature-fix"
        existing.mkdir()

        from cwm.errors import CWMError
        with pytest.raises(CWMError):
            manager.register_agent_symlink("feature-fix", existing)

    def test_refuses_existing_regular_file(self, project: Config, tmp_path: Path) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        existing = tmp_path / "feature-fix"
        existing.write_text("conflict")

        from cwm.errors import CWMError
        with pytest.raises(CWMError):
            manager.register_agent_symlink("feature-fix", existing)

    def test_normalises_double_dot_in_stored_path(
        self, project: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """register_agent_symlink must collapse '..' so later lookups from a
        different cwd resolve to the same string."""
        manager = WorktreeStateManager(project)
        manager.create_worktree("feature-fix")
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)
        # ../sibling resolves to tmp_path/sibling after collapsing '..'.
        registered = manager.register_agent_symlink("feature-fix", Path("../sibling"))

        assert ".." not in str(registered)
        # The stored link path itself (not its target) should be the
        # collapsed absolute path of ../sibling.
        assert str(registered) == os.path.abspath(str(sub / ".." / "sibling"))
