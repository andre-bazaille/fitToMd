import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import fitdecode

from fit_to_md.application.use_cases.sanitize_fit_fixture import SanitizeFitFixture
from fit_to_md.infrastructure.fitdecode.sanitizer import FitdecodeFixtureSanitizer

DEFAULT_TARGET_START = datetime(2020, 1, 1, 12, tzinfo=UTC)


def _iso_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "expected an ISO 8601 date and time"
        ) from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("the date and time must include a UTC offset")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fit-sanitize",
        description="Create a metadata-sanitized FIT file for public test fixtures.",
    )
    parser.add_argument("input", type=Path, help="Private source FIT file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Public output path; choose a name that does not reveal the source date.",
    )
    parser.add_argument(
        "--start-at",
        type=_iso_datetime,
        default=DEFAULT_TARGET_START,
        help="Replacement start in ISO 8601 format (default: 2020-01-01T12:00:00Z).",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    use_case: SanitizeFitFixture | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    source: Path = args.input
    destination: Path = args.output

    if not source.is_file():
        print(f"Input FIT file not found: {source}", file=stderr)
        return 2

    sanitizer = use_case or SanitizeFitFixture(FitdecodeFixtureSanitizer())
    try:
        summary = sanitizer.execute(source, destination, args.start_at)
    except (fitdecode.FitError, OSError, ValueError) as error:
        print(f"Unable to sanitize FIT file: {error}", file=stderr)
        return 1

    print(
        f"Created {destination} "
        f"({summary.removed_messages} messages and "
        f"{summary.removed_fields} field definitions removed; "
        f"{summary.shifted_timestamps} timestamps shifted).",
        file=stdout,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)
