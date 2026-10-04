"""Refreshing an activated shell after 'cwm ws build' changes the overlay install."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from cwm.cli.activate_cmd import generate_activate_script
from cwm.cli.main import cli
from cwm.core.config import Config
from cwm.core.overlay_state import NO_OVERLAY, OVERLAY_FP_VAR, overlay_fingerprint
from cwm.core.worktree_state import WorktreeStateManager
from cwm.util.shell_env import fish_quote
from tests.conftest import make_git_repo

FISH = shutil.which("fish")
needs_fish = pytest.mark.skipif(FISH is None, reason="fish shell not installed")
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
# The console script installed next to the interpreter running the tests.
REAL_CWM = Path(sys.executable).parent / "cwm"
needs_cwm = pytest.mark.skipif(not REAL_CWM.is_file(), reason="cwm console script not installed")


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CWM_", "_CWM_"))}
    env.update(extra)
    return env


def _install_package(install: Path, name: str, *, merged: bool = False, dsv: str = "") -> None:
    prefix = install if merged else install / name
    index = prefix / "share" / "colcon-core" / "packages"
    index.mkdir(parents=True, exist_ok=True)
    (index / name).write_text("")
    share = prefix / "share" / name
    share.mkdir(parents=True, exist_ok=True)
    (share / "package.dsv").write_text(dsv)
    (install / "local_setup.bash").write_text("# fake\n")


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


class TestOverlayFingerprint:
    def test_missing_overlay(self, tmp_path: Path) -> None:
        assert overlay_fingerprint(tmp_path / "install") == NO_OVERLAY

    def test_local_setup_without_packages_differs_from_missing(self, tmp_path: Path) -> None:
        install = tmp_path / "install"
        install.mkdir()
        (install / "local_setup.bash").write_text("")
        assert overlay_fingerprint(install) != NO_OVERLAY

    @pytest.mark.parametrize("merged", [False, True])
    def test_new_package_changes_fingerprint(self, tmp_path: Path, merged: bool) -> None:
        install = tmp_path / "install"
        _install_package(install, "pkg_a", merged=merged)
        first = overlay_fingerprint(install)
        assert first != NO_OVERLAY
        _install_package(install, "pkg_a", merged=merged)  # rebuild
        assert overlay_fingerprint(install) == first
        _install_package(install, "pkg_b", merged=merged)
        assert overlay_fingerprint(install) != first

    def test_hook_list_change_changes_fingerprint(self, tmp_path: Path) -> None:
        install = tmp_path / "install"
        _install_package(install, "pkg_a", dsv="source;share/pkg_a/hook/a.dsv\n")
        first = overlay_fingerprint(install)
        _install_package(install, "pkg_a", dsv="source;share/pkg_a/hook/a.dsv\nsource;x.dsv\n")
        assert overlay_fingerprint(install) != first

    def test_ignores_unrelated_directories(self, tmp_path: Path) -> None:
        install = tmp_path / "install"
        _install_package(install, "pkg_a")
        first = overlay_fingerprint(install)
        (install / "_local_setup_util_sh.py").write_text("")
        (install / "not_a_pkg" / "share").mkdir(parents=True)
        assert overlay_fingerprint(install) == first


# ---------------------------------------------------------------------------
# activation marker, ws build hint, __overlay-changed
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Config:
    underlay = tmp_path / "ros"
    underlay.mkdir()
    (underlay / "setup.bash").write_text("export ROS_DISTRO=fake\n")
    root = tmp_path / "project"
    root.mkdir()
    config = Config(underlay=str(underlay), repos=["my_repo"], project_root=root)
    for d in [config.cwm_dir / "worktrees", config.cwm_dir / "cache", config.worktrees_path]:
        d.mkdir(parents=True)
    make_git_repo(config.base_src_path / "my_repo")
    config.save()
    WorktreeStateManager(config).create_worktree("feat")
    return config


def _active_env(project: Config, fp: str | None, **extra: str) -> dict[str, str | None]:
    env: dict[str, str | None] = {
        "CWM_ACTIVE": "1",
        "CWM_PROJECT_ROOT": str(project.project_root),
        "CWM_WORKTREE": "feat",
        "CWM_WORKSPACE": str(project.worktree_ws_path("feat")),
        OVERLAY_FP_VAR: fp,
        "CWM_SHELL_REFRESH": None,
    }
    env.update(extra)
    return env


class TestActivationMarker:
    def test_bash_script_exports_and_unsets_marker(self, project: Config, monkeypatch) -> None:
        monkeypatch.chdir(project.project_root)
        monkeypatch.delenv("CWM_PROJECT_ROOT", raising=False)
        script = CliRunner().invoke(cli, ["activate", "feat"], catch_exceptions=False).output
        assert f"export {OVERLAY_FP_VAR}={NO_OVERLAY}" in script
        assert f"unset CWM_ACTIVE CWM_PROJECT_ROOT CWM_WORKTREE CWM_WORKSPACE {OVERLAY_FP_VAR}" in script

        _install_package(project.worktree_install_path("feat"), "pkg_a")
        script = CliRunner().invoke(cli, ["activate", "feat"], catch_exceptions=False).output
        fp = overlay_fingerprint(project.worktree_install_path("feat"))
        assert f"export {OVERLAY_FP_VAR}={fp}" in script

    def test_generate_defaults_to_no_overlay(self) -> None:
        script = generate_activate_script(
            branch="b", project_root="/p", workspace="/p/w", underlay="/u",
            base_install="/p/install", overlay_install="/p/w/install",
        )
        assert f"export {OVERLAY_FP_VAR}={NO_OVERLAY}" in script

    @needs_bash
    def test_fish_activation_carries_marker(self, project: Config, monkeypatch) -> None:
        monkeypatch.chdir(project.project_root)
        for var in ("CWM_PROJECT_ROOT", "CWM_ACTIVE", OVERLAY_FP_VAR):
            monkeypatch.delenv(var, raising=False)
        result = CliRunner().invoke(cli, ["activate", "--shell", "fish", "feat"], catch_exceptions=False)
        assert f"set -gx {OVERLAY_FP_VAR} '{NO_OVERLAY}'" in result.stdout


def _fake_build(project: Config, *packages: str):
    def run(subcommand: str, **_kwargs) -> None:
        assert subcommand == "build"
        for name in packages:
            _install_package(project.worktree_install_path("feat"), name)
    return run


class TestBuildHint:
    def _build(self, project: Config, monkeypatch, env, *args: str, packages=("pkg_a",)):
        monkeypatch.setattr("cwm.cli.build_cmd.run_workspace_colcon", _fake_build(project, *packages))
        return CliRunner().invoke(cli, ["ws", "build", *args], env=env, catch_exceptions=False)

    def test_hint_after_first_build(self, project: Config, monkeypatch) -> None:
        result = self._build(project, monkeypatch, _active_env(project, NO_OVERLAY))
        assert result.exit_code == 0, result.output
        assert "Re-activate" in result.stderr
        assert "source <(cwm activate feat)" in result.stderr
        assert "cwm activate --shell fish feat | source" in result.stderr

    def test_hint_after_new_package(self, project: Config, monkeypatch) -> None:
        install = project.worktree_install_path("feat")
        _install_package(install, "pkg_a")
        fp = overlay_fingerprint(install)
        result = self._build(project, monkeypatch, _active_env(project, fp), packages=("pkg_a",))
        assert "Re-activate" not in result.stderr  # rebuild of a known package
        result = self._build(project, monkeypatch, _active_env(project, fp), packages=("pkg_b",))
        assert "Re-activate" in result.stderr

    def test_no_hint_with_shell_integration(self, project: Config, monkeypatch) -> None:
        env = _active_env(project, NO_OVERLAY, CWM_SHELL_REFRESH="1")
        result = self._build(project, monkeypatch, env)
        assert result.exit_code == 0
        assert "Re-activate" not in result.output

    def test_no_hint_without_marker(self, project: Config, monkeypatch) -> None:
        result = self._build(project, monkeypatch, _active_env(project, None))
        assert "Re-activate" not in result.output

    def test_no_hint_on_dry_run(self, project: Config, monkeypatch) -> None:
        result = self._build(project, monkeypatch, _active_env(project, NO_OVERLAY), "--dry-run")
        assert "Re-activate" not in result.output


class TestOverlayChangedCommand:
    def _run(self, env) -> int:
        return CliRunner().invoke(cli, ["__overlay-changed"], env=env).exit_code

    def test_exit_codes(self, project: Config) -> None:
        assert self._run(_active_env(project, NO_OVERLAY)) == 1
        _install_package(project.worktree_install_path("feat"), "pkg_a")
        assert self._run(_active_env(project, NO_OVERLAY)) == 0
        fp = overlay_fingerprint(project.worktree_install_path("feat"))
        assert self._run(_active_env(project, fp)) == 1

    def test_not_active_or_no_marker(self, project: Config) -> None:
        _install_package(project.worktree_install_path("feat"), "pkg_a")
        assert self._run(_active_env(project, None)) == 1
        assert self._run(_active_env(project, NO_OVERLAY, CWM_ACTIVE=None)) == 1


# ---------------------------------------------------------------------------
# shell-init content
# ---------------------------------------------------------------------------


def _shell_init(*args: str) -> str:
    result = CliRunner().invoke(cli, ["shell-init", *args])
    assert result.exit_code == 0, result.output
    return result.output


class TestShellInitRefresh:
    def test_bash_function(self) -> None:
        out = _shell_init()
        assert "__cwm_refresh_if_stale() {" in out
        assert 'CWM_SHELL_REFRESH=1 command cwm "$@"' in out
        assert "command cwm __overlay-changed" in out
        assert 'command cwm activate "$__cwm_branch"' in out

    def test_fish_function(self) -> None:
        out = _shell_init("--shell", "fish")
        assert "function __cwm_refresh_if_stale" in out
        assert "CWM_SHELL_REFRESH=1 command cwm $argv" in out
        assert "command cwm __overlay-changed" in out
        assert "command cwm activate --shell fish $__cwm_branch | source" in out

    @needs_bash
    def test_bash_syntax(self, tmp_path: Path) -> None:
        path = tmp_path / "init.bash"
        path.write_text(_shell_init())
        subprocess.run(["bash", "-n", str(path)], check=True)

    @needs_fish
    def test_fish_syntax(self, tmp_path: Path) -> None:
        path = tmp_path / "init.fish"
        path.write_text(_shell_init("--shell", "fish"))
        subprocess.run([FISH, "-n", str(path)], check=True)


# ---------------------------------------------------------------------------
# real round trips with a stub colcon build
# ---------------------------------------------------------------------------


_STUB = """\
#!/bin/sh
# Stand-in for 'cwm': 'ws build' first fakes a colcon build that installs the
# packages listed in the pkgs file into the overlay, then everything runs the real cwm.
if [ "$1" = ws ] && [ "$2" = build ] && [ -s "{pkgs}" ]; then
    for p in $(cat "{pkgs}"); do
        mkdir -p "{install}/$p/share/colcon-core/packages" "{install}/$p/share/$p"
        : > "{install}/$p/share/colcon-core/packages/$p"
        : > "{install}/$p/share/$p/package.dsv"
        printf 'export AMENT_PREFIX_PATH="{install}/%s${{AMENT_PREFIX_PATH:+:$AMENT_PREFIX_PATH}}"\\n' "$p"
    done > "{install}/local_setup.bash.new"
    mv "{install}/local_setup.bash.new" "{install}/local_setup.bash"
    if [ -e "{fail}" ]; then
        exit 1
    fi
fi
exec "{real}" "$@"
"""


@pytest.fixture
def stub(project: Config, tmp_path: Path):
    """Return (env, install, pkgs file, fail flag) for running shells against a stub cwm."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    install = project.worktree_install_path("feat")
    pkgs = tmp_path / "pkgs"
    pkgs.write_text("")
    fail = tmp_path / "fail"
    script = bindir / "cwm"
    script.write_text(_STUB.format(install=install, pkgs=pkgs, fail=fail, real=REAL_CWM))
    script.chmod(0o755)
    env = _clean_env(
        PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        AMENT_PREFIX_PATH="/orig",
        ROS_DOMAIN_ID="3",
    )
    env.pop("ROS_AUTOMATIC_DISCOVERY_RANGE", None)
    env.pop("ROS_DISTRO", None)
    return env, install, pkgs, fail


@needs_bash
@needs_cwm
class TestBashRoundTrip:
    def test_shell_function_refreshes_after_build(self, project: Config, stub) -> None:
        env, install, pkgs, fail = stub
        probe = f"""
eval "$(cwm shell-init)"
PS1='> '
before_path="$PATH"
cwm activate feat >/dev/null
echo "A:$AMENT_PREFIX_PATH:$CWM_OVERLAY_FP"
echo pkg_a > {pkgs}
cwm ws build >/dev/null; echo "rc1:$?"
echo "B:$AMENT_PREFIX_PATH:$ROS_DOMAIN_ID:$ROS_DISTRO:$PS1"
echo "B-old:$_CWM_OLD_AMENT_PREFIX_PATH:$_CWM_OLD_ROS_DOMAIN_ID"
case "$PATH" in "{project.project_root}/.cwm/bin:$before_path") echo B-path-ok;; esac
printf 'pkg_a\\npkg_b\\n' > {pkgs}
cwm ws build >/dev/null; echo "rc2:$?"
echo "C:$AMENT_PREFIX_PATH"
cwm ws build >/dev/null; echo "rc3:$?"
printf 'pkg_a\\npkg_b\\npkg_c\\n' > {pkgs}; touch {fail}
cwm ws build >/dev/null; echo "rc4:$?"
echo "D:$AMENT_PREFIX_PATH"
cwm deactivate
echo "E:$AMENT_PREFIX_PATH:$ROS_DOMAIN_ID:${{ROS_DISTRO-unset}}:${{CWM_OVERLAY_FP-unset}}:$PS1"
[ "$PATH" = "$before_path" ] && echo E-path-ok
declare -f deactivate >/dev/null || echo E-deactivate-gone
"""
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", probe],
            env=env, cwd=project.project_root, capture_output=True, text=True,
        )
        out, err = result.stdout, result.stderr
        a, b, c = (f"{install}/{p}" for p in ("pkg_a", "pkg_b", "pkg_c"))
        assert f"A:/orig:{NO_OVERLAY}" in out, (out, err)
        assert "rc1:0" in out
        assert f"B:{a}:/orig:215:fake:[cwm:feat] > " in out, (out, err)
        assert "B-old:/orig:3" in out
        assert "B-path-ok" in out
        assert "rc2:0" in out
        assert f"C:{b}:{a}:/orig" in out
        assert "rc3:0" in out
        # A failed build does not refresh, even though the overlay changed.
        assert "rc4:1" in out
        assert f"D:{b}:{a}:/orig" in out
        assert "E:/orig:3:unset:unset:> " in out, (out, err)
        assert "E-path-ok" in out
        assert "E-deactivate-gone" in out
        # Refreshed exactly for the two builds that changed the overlay.
        assert err.count("re-activated 'feat'") == 2, err
        assert "Re-activate" not in err  # no hint when the shell refreshes itself

    def test_build_in_pipeline_reports_instead_of_refreshing(self, project: Config, stub) -> None:
        env, install, pkgs, _fail = stub
        probe = f"""
eval "$(cwm shell-init)"
cwm activate feat >/dev/null
echo pkg_a > {pkgs}
cwm ws build | cat >/dev/null
echo "A:$AMENT_PREFIX_PATH"
cwm ws build >/dev/null
echo "B:$AMENT_PREFIX_PATH"
"""
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", probe],
            env=env, cwd=project.project_root, capture_output=True, text=True,
        )
        assert "A:/orig" in result.stdout
        assert "ran in a subshell" in result.stderr
        # The next build in the shell itself still sees the stale activation.
        assert f"B:{install}/pkg_a:/orig" in result.stdout, (result.stdout, result.stderr)

    def test_plain_activation_prints_hint(self, project: Config, stub) -> None:
        env, install, pkgs, _fail = stub
        probe = f"""
source <(cwm activate feat) >/dev/null
cwm ws build >/dev/null 2>&1 && echo built-unchanged
cwm ws build 2>&1 | grep -q Re-activate && echo hint-before-change
echo pkg_a > {pkgs}
cwm ws build 2>&1 | grep -q Re-activate && echo hint-after-build
echo "AMENT:$AMENT_PREFIX_PATH"
source <(cwm activate feat) >/dev/null
echo "AMENT2:$AMENT_PREFIX_PATH"
cwm ws build 2>&1 | grep -q Re-activate && echo hint-after-reactivate
deactivate
echo "OUT:$AMENT_PREFIX_PATH"
"""
        out = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", probe],
            env=env, cwd=project.project_root, capture_output=True, text=True,
        ).stdout
        assert "built-unchanged" in out
        assert "hint-before-change" not in out
        assert "hint-after-build" in out
        assert "AMENT:/orig" in out
        assert f"AMENT2:{install}/pkg_a:/orig" in out
        assert "hint-after-reactivate" not in out
        assert "OUT:/orig" in out


@needs_bash
@needs_fish
@needs_cwm
class TestFishRoundTrip:
    def test_shell_function_refreshes_after_build(self, project: Config, stub) -> None:
        env, install, pkgs, fail = stub
        q_pkgs = fish_quote(str(pkgs))
        probe = f"""
cwm shell-init --shell fish | source
function fish_prompt; echo -n '> '; end
set -g before_path $PATH
cwm activate feat >/dev/null
echo "A:$AMENT_PREFIX_PATH:$CWM_OVERLAY_FP"
echo pkg_a > {q_pkgs}
cwm ws build >/dev/null; echo "rc1:$status"
echo "B:$AMENT_PREFIX_PATH:$ROS_DOMAIN_ID:$ROS_DISTRO:"(fish_prompt)
test (string join : $PATH) = (string join : {fish_quote(str(project.project_root))}/.cwm/bin $before_path); and echo B-path-ok
printf 'pkg_a\\npkg_b\\n' > {q_pkgs}
cwm ws build >/dev/null; echo "rc2:$status"
echo "C:$AMENT_PREFIX_PATH"
cwm ws build >/dev/null; echo "rc3:$status"
printf 'pkg_a\\npkg_b\\npkg_c\\n' > {q_pkgs}; touch {fish_quote(str(fail))}
cwm ws build >/dev/null; echo "rc4:$status"
echo "D:$AMENT_PREFIX_PATH"
cwm deactivate
echo "E:$AMENT_PREFIX_PATH:$ROS_DOMAIN_ID:"(fish_prompt)
set -q ROS_DISTRO; or echo E-distro-unset
set -q CWM_OVERLAY_FP; or echo E-fp-unset
test (string join : $PATH) = (string join : $before_path); and echo E-path-ok
functions -q deactivate; or echo E-deactivate-gone
set -q _CWM_FISH_VARS; or echo E-no-bookkeeping
"""
        result = subprocess.run(
            [FISH, "--no-config", "-c", probe],
            env=env, cwd=project.project_root, capture_output=True, text=True,
        )
        out, err = result.stdout, result.stderr
        a, b = (f"{install}/{p}" for p in ("pkg_a", "pkg_b"))
        assert f"A:/orig:{NO_OVERLAY}" in out, (out, err)
        assert "rc1:0" in out
        assert f"B:{a}:/orig:215:fake:[cwm:feat] > " in out, (out, err)
        assert "B-path-ok" in out
        assert "rc2:0" in out
        assert f"C:{b}:{a}:/orig" in out
        assert "rc3:0" in out
        assert "rc4:1" in out
        assert f"D:{b}:{a}:/orig" in out
        assert "E:/orig:3:> " in out, (out, err)
        for marker in ("E-distro-unset", "E-fp-unset", "E-path-ok", "E-deactivate-gone",
                       "E-no-bookkeeping"):
            assert marker in out, (marker, out, err)
        assert err.count("re-activated 'feat'") == 2, err
        assert "Re-activate" not in err
