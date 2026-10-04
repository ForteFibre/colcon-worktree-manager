"""fish shell support: env capture, activation script, shell-init."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cwm.cli.activate_cmd import generate_fish_activate_script
from cwm.cli.main import cli
from cwm.core.config import Config
from cwm.core.worktree_state import WorktreeStateManager
from cwm.errors import CWMError
from cwm.util.shell_env import EnvChanges, capture_env_changes, fish_quote
from tests.conftest import make_git_repo

FISH = shutil.which("fish")
needs_fish = pytest.mark.skipif(FISH is None, reason="fish shell not installed")
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CWM_", "_CWM_"))}
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# quoting / env capture
# ---------------------------------------------------------------------------


class TestFishQuote:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("plain", "'plain'"),
            ("with space", "'with space'"),
            ("it's", "'it\\'s'"),
            ('dq "x"', "'dq \"x\"'"),
            ("back\\slash", "'back\\\\slash'"),
            ("$HOME (cmd) *", "'$HOME (cmd) *'"),
            ("line1\nline2", "'line1\nline2'"),
            ("", "''"),
        ],
    )
    def test_quoting(self, value: str, expected: str) -> None:
        assert fish_quote(value) == expected

    @needs_fish
    @pytest.mark.parametrize(
        "value", ["it's \"both\" \\ $x (y) *", "line1\nline2", "", "  lead/trail  ", "a:b:c"]
    )
    def test_round_trips_through_fish(self, value: str) -> None:
        out = subprocess.run(
            [FISH, "--no-config", "-c", f"set -l v {fish_quote(value)}; printf '%s' \"$v\""],
            capture_output=True, text=True, check=True,
        ).stdout
        assert out == value


@needs_bash
class TestCaptureEnvChanges:
    def test_reports_set_changed_and_removed(self) -> None:
        env = _clean_env(KEEP="same", CHANGE="old", DROP="x")
        changes, _ = capture_env_changes(
            "export NEW='has space'; export CHANGE=new; unset DROP; export KEEP=same",
            env=env,
        )
        assert changes.set_vars == {"CHANGE": "new", "NEW": "has space"}
        assert changes.unset_vars == ["DROP"]

    def test_ignores_bash_bookkeeping_and_functions(self) -> None:
        changes, _ = capture_env_changes(
            "f() { :; }; export -f f; cd /; export SHLVL=9", env=_clean_env()
        )
        assert changes.set_vars == {}
        assert changes.unset_vars == []

    def test_script_stdout_does_not_corrupt_capture(self) -> None:
        changes, stderr = capture_env_changes(
            "echo 'noise=1'; printf 'X\\0Y=2\\0'; export A=1", env=_clean_env()
        )
        assert changes.set_vars == {"A": "1"}
        assert "noise=1" in stderr

    def test_multiline_value(self) -> None:
        changes, _ = capture_env_changes("export M=$'a\\nb'", env=_clean_env())
        assert changes.set_vars == {"M": "a\nb"}

    def test_failing_script_raises(self) -> None:
        with pytest.raises(CWMError, match="status 3"):
            capture_env_changes("exit 3", env=_clean_env())


# ---------------------------------------------------------------------------
# generated fish activation script
# ---------------------------------------------------------------------------


def _sample_script() -> str:
    changes = EnvChanges(
        set_vars={
            "CWM_ACTIVE": "1",
            "QUOTED": "it's \"q\" \\ x",
            "MULTI": "l1\nl2",
            "PATH": "/p/.cwm/bin:/usr/bin",
        },
        unset_vars=["GONE"],
    )
    return generate_fish_activate_script("feat/x", "/p/worktrees/feat-x_ws", changes, 215)


class TestFishActivateScript:
    def test_sets_and_erases_with_quoting(self) -> None:
        script = _sample_script()
        assert "set -gx CWM_ACTIVE '1'" in script
        assert "set -gx QUOTED 'it\\'s \"q\" \\\\ x'" in script
        assert "set -gx MULTI 'l1\nl2'" in script
        assert "set -gx PATH '/p/.cwm/bin:/usr/bin'" in script
        assert "set -e -g GONE" in script

    def test_snapshots_every_touched_variable(self) -> None:
        script = _sample_script()
        assert "set -g _CWM_FISH_VARS CWM_ACTIVE QUOTED MULTI PATH GONE" in script
        assert "function deactivate" in script
        assert "_CWM_OLD_$__cwm_var" in script
        assert "_CWM_WAS_UNSET_$__cwm_var" in script

    def test_mentions_domain_id(self) -> None:
        assert "ROS_DOMAIN_ID: 215" in _sample_script()

    @needs_fish
    def test_fish_syntax(self, tmp_path: Path) -> None:
        path = tmp_path / "act.fish"
        path.write_text(_sample_script())
        subprocess.run([FISH, "-n", str(path)], check=True)


@pytest.fixture
def project(tmp_path: Path) -> Config:
    underlay = tmp_path / "ros"
    underlay.mkdir()
    (underlay / "setup.bash").write_text(
        "echo 'underlay noise'\n"
        "export ROS_DISTRO=fake\n"
        "export TRICKY=\"it's a \\\"quoted\\\" value\"\n"
        "export MULTI=$'line1\\nline2'\n"
        "unset TO_BE_REMOVED\n"
    )
    root = tmp_path / "project"
    root.mkdir()
    config = Config(underlay=str(underlay), repos=["my_repo"], project_root=root)
    for d in [config.cwm_dir / "worktrees", config.cwm_dir / "cache", config.worktrees_path]:
        d.mkdir(parents=True)
    make_git_repo(config.base_src_path / "my_repo")
    config.save()
    WorktreeStateManager(config).create_worktree("feat")
    return config


def _fish_activate(project: Config, monkeypatch: pytest.MonkeyPatch):
    # Locate the project via cwd: an exported CWM_PROJECT_ROOT would already be
    # part of the baseline environment and so drop out of the diff.
    monkeypatch.chdir(project.project_root)
    monkeypatch.delenv("CWM_PROJECT_ROOT", raising=False)
    return CliRunner().invoke(cli, ["activate", "--shell", "fish", "feat"], catch_exceptions=False)


@needs_bash
class TestActivateShellFish:
    def test_emits_fish_script_from_bash_environment(self, project: Config, monkeypatch) -> None:
        monkeypatch.setenv("TO_BE_REMOVED", "bye")
        monkeypatch.delenv("CWM_ACTIVE", raising=False)
        result = _fish_activate(project, monkeypatch)
        assert result.exit_code == 0, result.output
        script = result.stdout
        assert "set -gx ROS_DISTRO 'fake'" in script
        assert "set -gx TRICKY 'it\\'s a \"quoted\" value'" in script
        assert "set -gx MULTI 'line1\nline2'" in script
        assert "set -e -g TO_BE_REMOVED" in script
        assert "set -gx ROS_DOMAIN_ID '215'" in script
        assert "set -gx ROS_AUTOMATIC_DISCOVERY_RANGE 'LOCALHOST'" in script
        assert f"set -gx CWM_PROJECT_ROOT {fish_quote(str(project.project_root))}" in script
        assert f"set -gx PATH '{project.project_root}/.cwm/bin:" in script
        # Setup-script chatter goes to stderr, never into the sourced script.
        assert "underlay noise" not in script
        assert "underlay noise" in result.stderr

    def test_refuses_when_already_active(self, project: Config) -> None:
        result = CliRunner().invoke(
            cli,
            ["activate", "--shell", "fish", "feat"],
            env={"CWM_PROJECT_ROOT": str(project.project_root), "CWM_ACTIVE": "1"},
        )
        assert result.exit_code != 0
        assert "deactivate" in result.output

    @needs_fish
    def test_source_and_deactivate_round_trip(self, project: Config, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.delenv("CWM_ACTIVE", raising=False)
        env = _clean_env(TO_BE_REMOVED="bye", ROS_DOMAIN_ID="3")
        env.pop("ROS_AUTOMATIC_DISCOVERY_RANGE", None)
        env.pop("TRICKY", None)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        script = _fish_activate(project, monkeypatch).stdout
        path = tmp_path / "act.fish"
        path.write_text(script)
        subprocess.run([FISH, "-n", str(path)], check=True)

        probe = f"""
set -g before_path $PATH
function fish_prompt; echo -n '> '; end
source {fish_quote(str(path))} >/dev/null
echo "in:$ROS_DOMAIN_ID:$ROS_AUTOMATIC_DISCOVERY_RANGE:$CWM_WORKTREE:$TRICKY"
set -q TO_BE_REMOVED; or echo in:removed
echo "in:path0:$PATH[1]"
echo "in:root:$CWM_PROJECT_ROOT"
echo "in:prompt:"(fish_prompt)
bash -c 'echo "child:$ROS_DOMAIN_ID"'
deactivate
echo "out:$ROS_DOMAIN_ID:$TO_BE_REMOVED"
set -q ROS_AUTOMATIC_DISCOVERY_RANGE; or echo out:range-unset
set -q CWM_WORKTREE; or echo out:worktree-unset
set -q TRICKY; or echo out:tricky-unset
test (string join : $PATH) = (string join : $before_path); and echo out:path-restored
echo "out:prompt:"(fish_prompt)
functions -q deactivate; or echo out:deactivate-gone
set -q _CWM_FISH_VARS; or echo out:no-bookkeeping
"""
        out = subprocess.run(
            [FISH, "--no-config", "-c", probe], env=env, capture_output=True, text=True, check=True
        ).stdout.splitlines()

        assert f"in:root:{project.project_root}" in out

        assert "in:215:LOCALHOST:feat:it's a \"quoted\" value" in out
        assert "in:removed" in out
        assert f"in:path0:{project.project_root}/.cwm/bin" in out
        assert "in:prompt:[cwm:feat] > " in out
        assert "child:215" in out
        assert "out:3:bye" in out
        for marker in ("range-unset", "worktree-unset", "tricky-unset", "path-restored",
                       "deactivate-gone", "no-bookkeeping"):
            assert f"out:{marker}" in out, (marker, out)
        assert "out:prompt:> " in out


# ---------------------------------------------------------------------------
# shell-init --shell fish
# ---------------------------------------------------------------------------


def _shell_init(*args: str) -> str:
    result = CliRunner().invoke(cli, ["shell-init", *args])
    assert result.exit_code == 0, result.output
    return result.output


class TestShellInitFish:
    def test_default_is_bash(self) -> None:
        assert "cwm()" in _shell_init()
        assert _shell_init("--shell", "zsh") == _shell_init("--shell", "bash")

    def test_fish_defines_functions(self) -> None:
        out = _shell_init("--shell", "fish")
        assert "function cwm" in out
        assert "function git" in out
        assert "function __cwm_in_project" in out
        assert "function __cwm_git_has_worktree" in out
        assert "cwm activate --shell fish" in out
        assert "$pipestatus[1]" in out
        assert "case -C -c --git-dir --work-tree --namespace --super-prefix" in out

    @needs_fish
    def test_fish_syntax(self, tmp_path: Path) -> None:
        path = tmp_path / "init.fish"
        path.write_text(_shell_init("--shell", "fish"))
        subprocess.run([FISH, "-n", str(path)], check=True)

    @needs_fish
    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["worktree", "add", "-b", "x", "../x"], "CWM worktree __git_hook add -b x ../x"),
            (["-C", "/x", "worktree", "list"], "CWM worktree __git_hook -C /x worktree list"),
            (["--no-pager", "worktree", "list"], "CWM worktree __git_hook --no-pager worktree list"),
            (["-C", "/x", "status"], "REAL_GIT -C /x status"),
            (["status"], "REAL_GIT status"),
            (["-C"], "REAL_GIT -C"),
        ],
    )
    def test_git_function_routing(self, tmp_path: Path, args: list[str], expected: str) -> None:
        """Run the fish 'git' function against stub cwm/git binaries."""
        log = tmp_path / "calls.log"
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name, tag in (("git", "REAL_GIT"), ("cwm", "CWM")):
            stub = bindir / name
            stub.write_text(f'#!/bin/sh\nprintf "{tag} %s\\n" "$*" >> "{log}"\n')
            stub.chmod(0o755)
        project = tmp_path / "proj" / "sub"
        project.mkdir(parents=True)
        (tmp_path / "proj" / ".cwm").mkdir()
        init = tmp_path / "init.fish"
        init.write_text(_shell_init("--shell", "fish"))

        quoted_args = " ".join(fish_quote(a) for a in args)
        env = _clean_env(PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
        subprocess.run(
            [FISH, "--no-config", "-c", f"source {fish_quote(str(init))}; cd {fish_quote(str(project))}; git {quoted_args}"],
            env=env, check=False, capture_output=True,
        )
        assert log.read_text().strip() == expected

    @needs_fish
    def test_git_function_outside_project_is_transparent(self, tmp_path: Path) -> None:
        log = tmp_path / "calls.log"
        bindir = tmp_path / "bin"
        bindir.mkdir()
        stub = bindir / "git"
        stub.write_text(f'#!/bin/sh\nprintf "REAL_GIT %s\\n" "$*" >> "{log}"\n')
        stub.chmod(0o755)
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        init = tmp_path / "init.fish"
        init.write_text(_shell_init("--shell", "fish"))
        env = _clean_env(PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
        subprocess.run(
            [FISH, "--no-config", "-c", f"source {fish_quote(str(init))}; cd {fish_quote(str(outside))}; git worktree list"],
            env=env, check=False, capture_output=True,
        )
        assert log.read_text().strip() == "REAL_GIT worktree list"
