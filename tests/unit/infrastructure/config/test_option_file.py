from pathlib import Path

import pytest

from fit_to_md.infrastructure.config import ConfigFileError, load_option_file


def test_load_option_file_reads_options_and_ignores_comments(tmp_path: Path) -> None:
    config_file = tmp_path / ".config"
    config_file.write_text(
        "# Personal defaults\n"
        "dynamics-step-size = 5\n"
        "elevation_source = hybrid\n",
        encoding="utf-8",
    )

    options = load_option_file(
        config_file,
        allowed_options=("dynamics-step-size", "elevation-source"),
    )

    assert options == {"dynamics-step-size": "5", "elevation-source": "hybrid"}


@pytest.mark.parametrize(
    ("contents", "expected_message"),
    [
        ("unknown-option = value\n", "unknown option"),
        ("weather-mode auto\n", "expected 'option = value'"),
        ("weather-mode = fit\nweather-mode = auto\n", "duplicate option"),
        ("weather-mode =\n", "non-empty option and value"),
    ],
)
def test_load_option_file_rejects_invalid_content(
    tmp_path: Path,
    contents: str,
    expected_message: str,
) -> None:
    config_file = tmp_path / ".config"
    config_file.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigFileError, match=expected_message):
        load_option_file(config_file, allowed_options=("weather-mode",))


def test_load_option_file_reports_an_unreadable_file(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing.config"

    with pytest.raises(ConfigFileError, match="unable to read configuration file"):
        load_option_file(missing_file, allowed_options=())
