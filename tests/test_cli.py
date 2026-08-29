from click.testing import CliRunner

from kindle_export_explorer.cli import main


def test_unified_cli_exposes_all_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "books" in result.output
    assert "overview" in result.output
    assert "reading" in result.output
    assert "resolve-reading" in result.output
