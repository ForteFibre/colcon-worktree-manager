"""CLI structure and help output tests."""

from __future__ import annotations

from click.testing import CliRunner

from cwm.cli.main import cli


def _listed_commands(output: str) -> list[str]:
    """Extract command names from a Click help page."""
    lines = output.splitlines()
    start = lines.index("Commands:") + 1
    commands: list[str] = []
    for line in lines[start:]:
        if not line.startswith("  "):
            break
        stripped = line.strip()
        if not stripped:
            continue
        commands.append(stripped.split()[0])
    return commands


class TestCliHelp:
    def test_top_level_help_lists_commands_in_public_order(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0, result.output
        assert _listed_commands(result.output) == [
            "init",
            "activate",
            "deactivate",
            "switch",
            "cd",
            "shell-init",
            "worktree",
            "repo",
            "ws",
            "inspect",
            "base",
            "doctor",
        ]

    def test_worktree_help_lists_commands_in_public_order(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["worktree", "--help"])

        assert result.exit_code == 0, result.output
        assert _listed_commands(result.output) == [
            "add",
            "focus",
            "remove",
            "list",
            "prune",
        ]

    def test_ws_help_lists_workspace_commands(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["ws", "--help"])

        assert result.exit_code == 0, result.output
        assert _listed_commands(result.output) == ["build", "test", "test-result", "clean", "status"]

    def test_inspect_help_lists_inspection_commands(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["inspect", "--help"])

        assert result.exit_code == 0, result.output
        assert _listed_commands(result.output) == ["env", "detect", "changed", "graph"]

    def test_top_level_doctor_is_registered(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["doctor", "--help"])

        assert result.exit_code == 0, result.output
        assert "--json" in result.output
