from collections.abc import Iterable
from pathlib import Path


class ConfigFileError(ValueError):
    """Raised when a CLI option file cannot be read or parsed."""


def load_option_file(path: Path, allowed_options: Iterable[str]) -> dict[str, str]:
    """Load ``option = value`` pairs from a CLI configuration file."""

    allowed = set(allowed_options)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConfigFileError(
            f"unable to read configuration file {path}: {error}"
        ) from error

    options: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        name, value = _parse_line(path, line_number, line)
        normalized_name = name.replace("_", "-")
        if normalized_name not in allowed:
            raise ConfigFileError(
                f"unknown option {name!r} in configuration file {path} at line {line_number}"
            )
        if normalized_name in options:
            raise ConfigFileError(
                f"duplicate option {name!r} in configuration file {path} at line {line_number}"
            )
        options[normalized_name] = value

    return options


def _parse_line(path: Path, line_number: int, line: str) -> tuple[str, str]:
    if "=" not in line:
        raise ConfigFileError(
            f"expected 'option = value' in configuration file {path} at line {line_number}"
        )

    name, value = (part.strip() for part in line.split("=", maxsplit=1))
    name = name.removeprefix("--")
    if not name or not value:
        raise ConfigFileError(
            f"expected a non-empty option and value in configuration file {path} at line {line_number}"
        )
    return name, value
