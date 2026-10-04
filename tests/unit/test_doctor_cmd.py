"""Tests for the top-level cwm doctor command."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cwm.cli.main import cli


@contextlib.contextmanager
def _patched(tmp_path: Path, *, base: dict, worktrees: list[dict], stale: list):
    with patch("cwm.cli.doctor_cmd.find_project_root", return_value=tmp_path), \
         patch("cwm.cli.doctor_cmd.Config.load", return_value=MagicMock()), \
         patch("cwm.core.worktree_state.WorktreeStateManager"), \
         patch("cwm.cli.doctor_cmd._collect_base", return_value=base), \
         patch("cwm.cli.doctor_cmd._collect_worktrees", return_value=worktrees), \
         patch("cwm.cli.doctor_cmd._scan_stale_build_dirs", return_value=stale):
        yield


def _wt(branch: str, *, exists: bool = True, built: bool = True) -> dict:
    return {
        "branch": branch,
        "repos": [{"repo": "my_repo", "exists": exists, "dirty": False, "ahead": 0}],
        "exists": exists,
        "built": built,
        "dirty": False,
        "ahead": 0,
        "created_at": "2026-06-03",
    }


class TestDoctor:
    def test_human_aggregates_base_and_worktrees(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched(
            tmp_path,
            base={"built": True, "dirty": False, "repos": [{"repo": "my_repo", "exists": True, "dirty": False}]},
            worktrees=[_wt("feat-x")],
            stale=[(Path("/b/p"), Path("/s/p"))],
        ):
            result = runner.invoke(cli, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "Base workspace" in result.output
        assert "1 stale build dir(s)" in result.output
        assert "cwm base doctor --fix" in result.output
        assert "feat-x" in result.output

    def test_missing_worktree_flagged(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched(
            tmp_path,
            base={"built": True, "dirty": False, "repos": [{"repo": "my_repo", "exists": True, "dirty": False}]},
            worktrees=[_wt("feat-gone", exists=False)],
            stale=[],
        ):
            result = runner.invoke(cli, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "missing (meta exists, ws gone)" in result.output

    def test_json_includes_stale_count(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with _patched(
            tmp_path,
            base={"built": False, "dirty": True, "repos": [{"repo": "my_repo", "exists": True, "dirty": False}]},
            worktrees=[_wt("feat-x")],
            stale=[(Path("/b/p"), Path("/s/p")), (Path("/b/q"), Path("/s/q"))],
        ):
            result = runner.invoke(cli, ["doctor", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["base"]["stale_build_dirs"] == 2
        assert payload["base"]["built"] is False
        assert payload["worktrees"][0]["branch"] == "feat-x"
