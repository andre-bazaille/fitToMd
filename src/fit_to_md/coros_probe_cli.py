"""Experimental command, separate from the normal FIT conversion CLI."""

import argparse
import json
import sys
import time
import webbrowser
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from fit_to_md.application.coros_connection import CorosConnection
from fit_to_md.domain.coros_connection import ISSUERS, ConnectionFailure
from fit_to_md.infrastructure.coros_gateway import CorosGateway, HttpsTransport
from fit_to_md.infrastructure.coros_vault import VaultSessionStore, system_backend
from fit_to_md.infrastructure.local_time import SystemLocalTimezone

_TOOL_NAMES = (
    "querySportRecords",
    "downloadActivityFitFiles",
    "queryActivityFitFileDownloadUrls",
)


def _open_browser(url: str) -> None:
    print("Authorize FitToMd in your browser.", file=sys.stderr, flush=True)
    try:
        opened = webbrowser.open(url)
    except (webbrowser.Error, OSError) as error:
        raise ConnectionFailure(
            "Could not open the browser for COROS login."
        ) from error
    if not opened:
        raise ConnectionFailure("Could not open the browser for COROS login.")


def _calendar_date(value: str) -> date:
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value or result > date.today():
            raise ValueError
        return result
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Use a non-future date in YYYY-MM-DD format."
        ) from error


def _print_activity_response(result: dict[str, object]) -> None:
    print("COROS activity response (local inspection only):", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)


def _local_zone() -> ZoneInfo:
    return SystemLocalTimezone(extra="coros-probe").resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fit-coros-probe")
    parser.add_argument("--region", choices=ISSUERS, default="eu")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="Authorize COROS in your browser")
    commands.add_parser("identity", help="Print an opaque account identity fingerprint")
    probe = commands.add_parser(
        "probe", help="Verify session and list relevant tool availability"
    )
    probe.add_argument(
        "--refresh", action="store_true", help="Exercise token rotation now"
    )
    commands.add_parser(
        "logout", help="Remove this prototype session from the local vault"
    )
    activities = commands.add_parser(
        "activities", help="Check whether COROS lists activities for a specific day"
    )
    activities.add_argument(
        "--inspect-dates",
        action="store_true",
        help="Inspect candidate Unix-second timestamps in UTC and the local timezone",
    )
    activities.add_argument(
        "--on", type=_calendar_date, required=True, metavar="YYYY-MM-DD"
    )
    activities.add_argument("--through", type=_calendar_date, metavar="YYYY-MM-DD")
    activities.add_argument(
        "--limit", type=int, choices=range(1, 21), default=20, metavar="1..20"
    )
    activities.add_argument(
        "--inspect-response",
        action="store_true",
        help="Show the activity tool result locally; it can contain private activity data",
    )
    fit = commands.add_parser(
        "fit", help="Download one FIT from a day with exactly one activity"
    )
    fit.add_argument("--on", type=_calendar_date, required=True, metavar="YYYY-MM-DD")
    fit.add_argument("--output", type=Path, required=True, metavar="FILE.fit")
    args = parser.parse_args(argv)
    if (
        args.command == "activities"
        and args.through is not None
        and args.through < args.on
    ):
        parser.error("--through cannot precede --on.")

    try:
        zone = (
            _local_zone()
            if args.command == "activities" and args.inspect_dates
            else None
        )
        if args.command == "fit" and (
            args.output.suffix.lower() != ".fit"
            or not args.output.parent.is_dir()
            or args.output.exists()
            or args.output.is_symlink()
        ):
            raise ConnectionFailure("Choose a new .fit file in an existing directory.")
        connection = CorosConnection(
            CorosGateway(
                args.region,
                HttpsTransport(),
                now=time.time,
                monotonic=time.monotonic,
                sleep=time.sleep,
            ),
            VaultSessionStore(args.region, system_backend()),
            time.time,
        )
        if args.command == "login":
            connection.login(_open_browser)
            print("COROS session saved in the system vault.")
        elif args.command == "identity":
            print(
                f"COROS identity fingerprint: {connection.probe_identity().fingerprint}"
            )
        elif args.command == "probe":
            result = connection.probe(force_refresh=args.refresh)
            session_action = "renewed" if result.refreshed else "reused"
            print(f"MCP {result.protocol_version}; session {session_action}.")
            for name in _TOOL_NAMES:
                print(f"{name}: {'available' if name in result.tools else 'absent'}")
            if _TOOL_NAMES[0] not in result.tools or result.tools.isdisjoint(
                _TOOL_NAMES[1:]
            ):
                raise ConnectionFailure(
                    "Required COROS catalog or FIT tools are missing."
                )
        elif args.command == "activities":
            activity_result = connection.probe_activities(
                args.on,
                inspect=_print_activity_response if args.inspect_response else None,
                through=args.through,
                limit=args.limit,
            )
            if args.through is None:
                print(f"Requested day: {args.on.isoformat()}.")
            else:
                print(
                    f"Requested period: {args.on.isoformat()} to {args.through.isoformat()}."
                )
            print(f"Requested limit: {args.limit}.")
            if activity_result.returned_period is not None:
                start, end = activity_result.returned_period
                print(f"COROS reported period: {start} to {end}.")
            print(f"Activities {activity_result.count_kind}: {activity_result.count}.")
            if zone is not None:
                print(f"Local timezone: {zone.key}.")
                print(
                    "Candidate timestamp unit: Unix seconds; server semantics unverified."
                )
                for observation in activity_result.dates:
                    print(f"Activity fingerprint: {observation.fingerprint}")
                    for label, instant in (
                        ("start", observation.start),
                        ("end", observation.end),
                    ):
                        if instant is not None:
                            try:
                                local = instant.astimezone(zone).isoformat()
                                seconds = int(instant.timestamp())
                            except (OverflowError, ValueError, OSError) as error:
                                raise ConnectionFailure(
                                    "Activity timestamp cannot be represented in the local timezone."
                                ) from error
                            print(
                                f"{label}: {seconds}; UTC {instant.isoformat()}; local {local}"
                            )
                    if observation.problem:
                        print(observation.problem, file=sys.stderr)
                if len(activity_result.dates) != activity_result.count or any(
                    observation.problem for observation in activity_result.dates
                ):
                    raise ConnectionFailure(
                        "Incomplete or invalid activity date diagnostics."
                    )
        elif args.command == "fit":
            binary = connection.download_sample(args.on)
            try:
                with args.output.open("xb") as output:
                    output.write(binary)
            except OSError as error:
                raise ConnectionFailure("Could not write the new FIT file.") from error
            print(f"COROS FIT saved ({len(binary)} bytes).")
        else:
            connection.logout()
            print("Local COROS session removed.")
        return 0
    except ConnectionFailure as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("COROS prototype interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
