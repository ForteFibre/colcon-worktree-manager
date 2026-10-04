"""Multi-repo worktrees: lifecycle, branch resolution, rollback, focus, changeset."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from cwm.cli.main import cli
from cwm.core.changeset import compute_changeset
from cwm.core.config import Config
from cwm.core.worktree_state import WorktreeMeta, WorktreeStateManager
from cwm.errors import (
    CWMError,
    GitError,
    RepoNameCollisionError,
    RepoNotInWorktreeError,
    WorktreeNotFoundError,
)
from cwm.util import git as gitutil
from tests.conftest import GIT_ENV, make_git_repo, make_package_xml


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GIT_ENV
    )
    return result.stdout.strip()


def _commit_package(repo: Path, name: str, deps: list[str] | None = None) -> None:
    pkg = repo / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "package.xml").write_text(make_package_xml(name, deps))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"add {name}")


def _make_project(tmp_path: Path, repos: list[str], default: list[str] | None = None) -> Config:
    root = tmp_path / "project"
    root.mkdir()
    config = Config(
        underlay="/opt/ros/jazzy",
        repos=list(repos if default is None else default),
        project_root=root,
    )
    for d in [config.cwm_dir / "worktrees", config.cwm_dir / "cache", config.worktrees_path]:
        d.mkdir(parents=True)
    for rel in repos:
        make_git_repo(config.base_src_path / rel)
    config.save()
    return config


@pytest.fixture
def project(tmp_path: Path) -> Config:
    """Project with two repositories, both in the default set."""
    return _make_project(tmp_path, ["repo_a", "group/repo_b"])


def _registered_worktrees(repo: Path) -> list[Path]:
    return [info.path for info in gitutil.worktree_list(cwd=repo)][1:]


# ---------------------------------------------------------------------------
# create / remove
# ---------------------------------------------------------------------------


class TestCreateMultiRepo:
    def test_checks_out_every_default_repo(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat/x")

        for name in ("repo_a", "repo_b"):
            checkout = ws / "src" / name
            assert (checkout / ".git").exists()
            assert _git(checkout, "rev-parse", "--abbrev-ref", "HEAD") == "feat/x"

        meta = manager.get_worktree_meta("feat/x")
        assert list(meta.repos) == ["repo_a", "group/repo_b"]
        for rel, state in meta.repos.items():
            assert state.base_sha == _git(project.repo_path(rel), "rev-parse", "HEAD")
            assert state.base_branch == "main"

    def test_explicit_subset(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat", ["group/repo_b"])

        assert not (ws / "src" / "repo_a").exists()
        assert (ws / "src" / "repo_b").is_dir()
        assert manager.get_worktree_meta("feat").repo_names == ["group/repo_b"]

    def test_repo_may_be_named_by_basename(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feat", ["repo_b"])
        assert manager.get_worktree_meta("feat").repo_names == ["group/repo_b"]

    def test_basename_collision_is_rejected_before_any_change(self, tmp_path: Path) -> None:
        config = _make_project(tmp_path, ["a/dup", "b/dup"])
        manager = WorktreeStateManager(config)

        with pytest.raises(RepoNameCollisionError):
            manager.create_worktree("feat")

        assert not config.worktree_ws_path("feat").exists()
        assert not config.worktree_meta_path("feat").exists()

    def test_unknown_repo_is_rejected(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        with pytest.raises(CWMError, match="nope"):
            manager.create_worktree("feat", ["nope"])
        assert not project.worktree_ws_path("feat").exists()


class TestBranchResolution:
    def test_existing_local_branch_is_checked_out(self, project: Config) -> None:
        base = project.repo_path("repo_a")
        _git(base, "branch", "feat")
        _git(base, "checkout", "feat")
        _commit_package(base, "pkg_on_branch")
        branch_sha = _git(base, "rev-parse", "HEAD")
        _git(base, "checkout", "main")

        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat", ["repo_a"])

        checkout = ws / "src" / "repo_a"
        assert _git(checkout, "rev-parse", "HEAD") == branch_sha
        assert (checkout / "pkg_on_branch" / "package.xml").exists()

    def test_remote_only_branch_creates_tracking_branch(self, tmp_path: Path) -> None:
        # A bare 'origin' that has 'feat' while the clone under src/ does not.
        origin = tmp_path / "origin.git"
        seed = tmp_path / "seed"
        make_git_repo(seed)
        subprocess.run(["git", "clone", "--bare", str(seed), str(origin)], check=True,
                       capture_output=True, env=GIT_ENV)

        config = _make_project(tmp_path, [], default=["remote_repo"])
        subprocess.run(["git", "clone", str(origin), str(config.repo_path("remote_repo"))],
                       check=True, capture_output=True, env=GIT_ENV)

        _git(seed, "remote", "add", "origin", str(origin))
        _git(seed, "checkout", "-b", "feat")
        _commit_package(seed, "remote_pkg")
        _git(seed, "push", "origin", "feat")
        remote_sha = _git(seed, "rev-parse", "HEAD")

        base = config.repo_path("remote_repo")
        assert not gitutil.branch_exists("feat", cwd=base)
        assert not gitutil.remote_branch_exists("feat", cwd=base)  # not fetched yet

        manager = WorktreeStateManager(config)
        ws = manager.create_worktree("feat")

        checkout = ws / "src" / "remote_repo"
        assert _git(checkout, "rev-parse", "HEAD") == remote_sha
        assert _git(checkout, "rev-parse", "--abbrev-ref", "feat@{upstream}") == "origin/feat"

    def test_new_branch_is_created_from_base_head(self, project: Config) -> None:
        base = project.repo_path("repo_a")
        head = _git(base, "rev-parse", "HEAD")

        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("brand-new", ["repo_a"])

        assert gitutil.branch_exists("brand-new", cwd=base)
        assert _git(ws / "src" / "repo_a", "rev-parse", "HEAD") == head

    def test_fetch_failure_is_tolerated(self, tmp_path: Path) -> None:
        """An unreachable origin must not block creating a new branch."""
        config = _make_project(tmp_path, ["repo_a"])
        base = config.repo_path("repo_a")
        _git(base, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))

        manager = WorktreeStateManager(config)
        ws = manager.create_worktree("offline")
        assert (ws / "src" / "repo_a" / ".git").exists()


class TestRollback:
    def test_failure_in_second_repo_rolls_back_first(self, project: Config) -> None:
        # The base checkout of repo_b is on 'feat', so git refuses a second
        # worktree on the same branch.
        base_b = project.repo_path("group/repo_b")
        _git(base_b, "checkout", "-b", "feat")

        manager = WorktreeStateManager(project)
        with pytest.raises(GitError):
            manager.create_worktree("feat")

        assert not project.worktree_ws_path("feat").exists()
        assert not project.worktree_meta_path("feat").exists()
        base_a = project.repo_path("repo_a")
        assert _registered_worktrees(base_a) == []
        # The branch was created by the failed call, so it is deleted again.
        assert not gitutil.branch_exists("feat", cwd=base_a)
        # The pre-existing branch in repo_b is untouched.
        assert gitutil.branch_exists("feat", cwd=base_b)

    def test_rollback_keeps_preexisting_branch(self, project: Config) -> None:
        base_a = project.repo_path("repo_a")
        _git(base_a, "branch", "feat")
        _git(project.repo_path("group/repo_b"), "checkout", "-b", "feat")

        manager = WorktreeStateManager(project)
        with pytest.raises(GitError):
            manager.create_worktree("feat")

        assert gitutil.branch_exists("feat", cwd=base_a)
        assert _registered_worktrees(base_a) == []


class TestRemoveMultiRepo:
    def test_removes_every_checkout(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feat")

        manager.remove_worktree("feat", delete_branch=True)

        assert not project.worktree_ws_path("feat").exists()
        assert not project.worktree_meta_path("feat").exists()
        for rel in project.repos:
            assert _registered_worktrees(project.repo_path(rel)) == []
            assert not gitutil.branch_exists("feat", cwd=project.repo_path(rel))

    def test_dirty_repo_blocks_removal_of_all(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat")
        (ws / "src" / "repo_b" / "dirty.txt").write_text("x")

        with pytest.raises(GitError, match="group/repo_b"):
            manager.remove_worktree("feat")

        # Nothing was removed.
        assert (ws / "src" / "repo_a").is_dir()
        assert (ws / "src" / "repo_b").is_dir()
        assert project.worktree_meta_path("feat").exists()

        manager.remove_worktree("feat", force=True)
        assert not ws.exists()

    def test_prune_cleans_every_repo(self, project: Config) -> None:
        import shutil

        manager = WorktreeStateManager(project)
        manager.create_worktree("feat")
        shutil.rmtree(project.worktree_ws_path("feat"))

        assert manager.prune_stale() == ["feat"]
        for rel in project.repos:
            assert _registered_worktrees(project.repo_path(rel)) == []


# ---------------------------------------------------------------------------
# focus
# ---------------------------------------------------------------------------


class TestFocus:
    def test_add_then_remove_repo(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat", ["repo_a"])

        assert manager.add_repos("feat", ["group/repo_b"]) == ["group/repo_b"]
        assert (ws / "src" / "repo_b" / ".git").exists()
        assert manager.get_worktree_meta("feat").repo_names == ["repo_a", "group/repo_b"]

        assert manager.remove_repos("feat", ["repo_a"]) == ["repo_a"]
        assert not (ws / "src" / "repo_a").exists()
        assert _registered_worktrees(project.repo_path("repo_a")) == []
        assert manager.get_worktree_meta("feat").repo_names == ["group/repo_b"]

    def test_add_uses_branch_resolution(self, project: Config) -> None:
        base_b = project.repo_path("group/repo_b")
        _git(base_b, "branch", "feat")
        _git(base_b, "checkout", "feat")
        _commit_package(base_b, "pkg_b")
        sha = _git(base_b, "rev-parse", "HEAD")
        _git(base_b, "checkout", "main")

        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat", ["repo_a"])
        manager.add_repos("feat", ["group/repo_b"])
        assert _git(ws / "src" / "repo_b", "rev-parse", "HEAD") == sha

    def test_add_existing_repo_errors(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feat", ["repo_a"])
        with pytest.raises(CWMError, match="already in worktree"):
            manager.add_repos("feat", ["repo_a"])

    def test_add_to_missing_worktree_errors(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        with pytest.raises(WorktreeNotFoundError):
            manager.add_repos("nope", ["repo_a"])

    def test_add_failure_keeps_existing_worktree(self, project: Config) -> None:
        _git(project.repo_path("group/repo_b"), "checkout", "-b", "feat")
        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat", ["repo_a"])

        with pytest.raises(GitError):
            manager.add_repos("feat", ["group/repo_b"])

        assert (ws / "src" / "repo_a").is_dir()
        assert manager.get_worktree_meta("feat").repo_names == ["repo_a"]

    def test_remove_last_repo_errors(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feat", ["repo_a"])
        with pytest.raises(CWMError, match="worktree remove"):
            manager.remove_repos("feat", ["repo_a"])

    def test_remove_unknown_repo_errors(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        manager.create_worktree("feat")
        with pytest.raises(RepoNotInWorktreeError):
            manager.remove_repos("feat", ["nope"])

    def test_remove_dirty_repo_requires_force(self, project: Config) -> None:
        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat")
        (ws / "src" / "repo_a" / "dirty.txt").write_text("x")

        with pytest.raises(GitError):
            manager.remove_repos("feat", ["repo_a"])
        assert (ws / "src" / "repo_a").is_dir()

        manager.remove_repos("feat", ["repo_a"], force=True)
        assert not (ws / "src" / "repo_a").exists()


# ---------------------------------------------------------------------------
# metadata migration
# ---------------------------------------------------------------------------


class TestMetaMigration:
    def test_legacy_single_repo_meta_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "feat.yaml"
        path.write_text(yaml.safe_dump({
            "branch": "feat",
            "created_at": "2026-01-01",
            "repo": "autoware.universe",
            "base_sha": "abc123",
            "base_branch": "main",
            "agent_symlinks": ["/tmp/x"],
        }))

        meta = WorktreeMeta.load(path)
        assert meta.repo_names == ["autoware.universe"]
        assert meta.repos["autoware.universe"].base_sha == "abc123"
        assert meta.repos["autoware.universe"].base_branch == "main"
        assert meta.agent_symlinks == ["/tmp/x"]

        meta.save(path)
        data = yaml.safe_load(path.read_text())
        assert "repo" not in data and "base_sha" not in data
        assert data["repos"] == {"autoware.universe": {"base_sha": "abc123", "base_branch": "main"}}

    def test_legacy_worktree_can_be_removed(self, tmp_path: Path) -> None:
        """A worktree created by the single-repo CWM must still be removable."""
        config = _make_project(tmp_path, ["my_repo"])
        manager = WorktreeStateManager(config)
        ws = manager.create_worktree("feat")
        meta_path = config.worktree_meta_path("feat")
        meta_path.write_text(yaml.safe_dump({
            "branch": "feat",
            "created_at": "2026-01-01",
            "repo": "my_repo",
            "base_sha": _git(config.repo_path("my_repo"), "rev-parse", "HEAD"),
            "base_branch": "main",
            "agent_symlinks": [],
        }))

        manager.remove_worktree("feat")
        assert not ws.exists()
        assert _registered_worktrees(config.repo_path("my_repo")) == []


# ---------------------------------------------------------------------------
# changeset across repositories
# ---------------------------------------------------------------------------


class TestChangesetAcrossRepos:
    @pytest.fixture
    def pkg_project(self, tmp_path: Path) -> Config:
        config = _make_project(tmp_path, ["repo_a", "group/repo_b"])
        _commit_package(config.repo_path("repo_a"), "pkg_a")
        _commit_package(config.repo_path("group/repo_b"), "pkg_b")
        _commit_package(config.repo_path("group/repo_b"), "pkg_c", deps=["pkg_a"])
        return config

    def test_union_of_changes_in_both_repos(self, pkg_project: Config) -> None:
        manager = WorktreeStateManager(pkg_project)
        ws = manager.create_worktree("feat")
        (ws / "src" / "repo_a" / "pkg_a" / "a.cpp").write_text("// a")
        _git(ws / "src" / "repo_a", "add", "-A")
        (ws / "src" / "repo_b" / "pkg_b" / "b.cpp").write_text("// b")
        _git(ws / "src" / "repo_b", "add", "-A")

        changeset = compute_changeset(pkg_project, "feat")
        assert changeset.package_count == 3
        assert changeset.changed == {"pkg_a", "pkg_b"}
        # pkg_c (repo_b) depends on pkg_a (repo_a): reverse deps cross repos.
        assert changeset.affected == {"pkg_c"}
        assert changeset.build_order.index("pkg_a") < changeset.build_order.index("pkg_c")

    def test_each_repo_diffs_against_its_own_base(self, pkg_project: Config) -> None:
        manager = WorktreeStateManager(pkg_project)
        ws = manager.create_worktree("feat")
        checkout_b = ws / "src" / "repo_b"
        (checkout_b / "pkg_b" / "b.cpp").write_text("// b")
        _git(checkout_b, "add", "-A")
        _git(checkout_b, "commit", "-m", "change b")

        changeset = compute_changeset(pkg_project, "feat")
        assert changeset.changed == {"pkg_b"}
        assert changeset.affected == set()

    def test_focus_added_repo_takes_part(self, pkg_project: Config) -> None:
        manager = WorktreeStateManager(pkg_project)
        ws = manager.create_worktree("feat", ["group/repo_b"])
        manager.add_repos("feat", ["repo_a"])
        (ws / "src" / "repo_a" / "pkg_a" / "a.cpp").write_text("// a")
        _git(ws / "src" / "repo_a", "add", "-A")

        changeset = compute_changeset(pkg_project, "feat")
        assert changeset.changed == {"pkg_a"}
        assert changeset.affected == {"pkg_c"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(project: Config, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.chdir(project.project_root)
    monkeypatch.delenv("CWM_PROJECT_ROOT", raising=False)
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


class TestWorktreeCli:
    def test_add_with_repos_option_and_list_json(self, project: Config, monkeypatch) -> None:
        result = _cli(project, monkeypatch, "worktree", "add", "feat/x", "--repos", "repo_a,group/repo_b", "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert [r["repo"] for r in payload["repos"]] == ["repo_a", "group/repo_b"]
        assert payload["repos"][1]["src_path"].endswith("feat-x_ws/src/repo_b")

        result = _cli(project, monkeypatch, "worktree", "list", "--json")
        listing = json.loads(result.output)
        assert [r["repo"] for r in listing["worktrees"][0]["repos"]] == ["repo_a", "group/repo_b"]

    def test_add_with_subset(self, project: Config, monkeypatch) -> None:
        result = _cli(project, monkeypatch, "worktree", "add", "feat", "--repos", "group/repo_b")
        assert result.exit_code == 0, result.output
        assert WorktreeStateManager(project).get_worktree_meta("feat").repo_names == ["group/repo_b"]

    def test_focus_cli(self, project: Config, monkeypatch) -> None:
        _cli(project, monkeypatch, "worktree", "add", "feat", "--repos", "repo_a")

        result = _cli(project, monkeypatch, "worktree", "focus", "feat", "--add", "group/repo_b", "--json")
        payload = json.loads(result.output)
        assert payload["added"] == ["group/repo_b"]

        result = _cli(project, monkeypatch, "worktree", "focus", "feat", "--list")
        assert "repo_a" in result.output and "group/repo_b" in result.output

        result = _cli(project, monkeypatch, "worktree", "focus", "feat", "--rm", "repo_a", "--json")
        payload = json.loads(result.output)
        assert payload["removed"] == ["repo_a"]
        assert [r["repo"] for r in payload["repos"]] == ["group/repo_b"]

        result = CliRunner().invoke(cli, ["worktree", "focus", "feat", "--remove", "group/repo_b"])
        assert result.exit_code != 0
        assert "worktree remove" in result.output

    def test_status_reports_each_repo(self, project: Config, monkeypatch) -> None:
        manager = WorktreeStateManager(project)
        ws = manager.create_worktree("feat")
        (ws / "src" / "repo_b" / "dirty.txt").write_text("x")

        result = _cli(project, monkeypatch, "ws", "status", "--json")
        payload = json.loads(result.output)
        assert [r["repo"] for r in payload["base"]["repos"]] == ["repo_a", "group/repo_b"]
        wt = payload["worktrees"][0]
        assert wt["dirty"] is True
        assert {r["repo"]: r["dirty"] for r in wt["repos"]} == {"repo_a": False, "group/repo_b": True}

        result = _cli(project, monkeypatch, "ws", "status")
        assert "[repo_a, group/repo_b*]" in result.output

    def test_detect_lists_repos(self, project: Config, monkeypatch) -> None:
        result = _cli(project, monkeypatch, "inspect", "detect")
        assert json.loads(result.output)["repos"] == ["repo_a", "group/repo_b"]

    def test_cd_resolves_named_repo(self, project: Config, monkeypatch) -> None:
        WorktreeStateManager(project).create_worktree("feat")
        monkeypatch.delenv("CWM_WORKSPACE", raising=False)

        result = _cli(project, monkeypatch, "__cd-resolve", "feat", "repo_b")
        assert result.output.strip() == str(project.worktree_ws_path("feat") / "src" / "repo_b")

        # With several repos, 'switch' (auto-subrepo) stays at the workspace root.
        result = _cli(project, monkeypatch, "__cd-resolve", "--auto-subrepo", "feat")
        assert result.output.strip() == str(project.worktree_ws_path("feat"))


# ---------------------------------------------------------------------------
# git hook
# ---------------------------------------------------------------------------


class TestHookMultiRepo:
    def _hook(self, cwd: Path, project: Config, monkeypatch, *args: str):
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CWM_PROJECT_ROOT", str(project.project_root))
        return CliRunner().invoke(cli, ["worktree", "__git_hook", *args], catch_exceptions=False)

    def test_add_from_inside_repo_uses_that_repo(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        link = tmp_path / "feat-link"
        result = self._hook(project.repo_path("group/repo_b"), project, monkeypatch,
                            "add", "-b", "feat", str(link))
        assert result.exit_code == 0, result.stderr
        assert WorktreeStateManager(project).get_worktree_meta("feat").repo_names == ["group/repo_b"]
        assert link.resolve() == project.worktree_ws_path("feat").resolve()

    def test_add_from_project_root_uses_default_set(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        result = self._hook(project.project_root, project, monkeypatch,
                            "add", "-b", "feat", str(tmp_path / "l"))
        assert result.exit_code == 0, result.stderr
        assert WorktreeStateManager(project).get_worktree_meta("feat").repo_names == [
            "repo_a", "group/repo_b",
        ]

    def test_add_from_second_repo_extends_existing_worktree(
        self, project: Config, tmp_path: Path, monkeypatch
    ) -> None:
        link_b = tmp_path / "link-b"
        link_a = tmp_path / "link-a"
        self._hook(project.repo_path("group/repo_b"), project, monkeypatch, "add", "-b", "feat", str(link_b))
        result = self._hook(project.repo_path("repo_a"), project, monkeypatch,
                            "add", "-b", "feat", str(link_a))

        assert result.exit_code == 0, result.stderr
        assert "added 'repo_a'" in result.stderr
        meta = WorktreeStateManager(project).get_worktree_meta("feat")
        assert meta.repo_names == ["group/repo_b", "repo_a"]
        assert str(link_a) in meta.agent_symlinks and str(link_b) in meta.agent_symlinks

    def test_extend_symlink_failure_only_rolls_back_added_repo(
        self, project: Config, tmp_path: Path, monkeypatch
    ) -> None:
        self._hook(project.repo_path("group/repo_b"), project, monkeypatch,
                   "add", "-b", "feat", str(tmp_path / "link-b"))
        blocker = tmp_path / "blocker"
        blocker.mkdir()
        result = self._hook(project.repo_path("repo_a"), project, monkeypatch,
                            "add", "-b", "feat", str(blocker))

        assert result.exit_code == 1
        assert project.worktree_ws_path("feat").is_dir()
        assert WorktreeStateManager(project).get_worktree_meta("feat").repo_names == ["group/repo_b"]

    def test_list_collapses_repos_into_one_row(self, project: Config, tmp_path: Path, monkeypatch) -> None:
        WorktreeStateManager(project).create_worktree("feat")
        result = self._hook(project.project_root, project, monkeypatch, "list", "--porcelain")
        assert result.output.count("branch refs/heads/feat") == 1
        # Both base checkouts are still listed.
        assert str(project.repo_path("repo_a")) in result.output
        assert str(project.repo_path("group/repo_b")) in result.output
