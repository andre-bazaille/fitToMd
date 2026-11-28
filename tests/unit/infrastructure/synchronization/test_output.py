import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from time import monotonic, sleep
from zoneinfo import ZoneInfo

import pytest

from fit_to_md.application.synchronization_storage import (
    Attempts,
    ConversionOptions,
    FailureReason,
    OutputAccountMismatch,
    OutputBusy,
    OutputConflict,
    Stage,
    StorageFailure,
    VaultReference,
)
from fit_to_md.domain.synchronization.model import (
    AccountIdentity,
    AcquisitionState,
    ActivityIdentity,
    ConversionState,
    Reference,
    RemoteActivity,
)
from fit_to_md.infrastructure.synchronization import files, output
from fit_to_md.infrastructure.synchronization.output import SqliteOutput
from fit_to_md.infrastructure.synchronization.registry import SqliteRegistry

pytestmark = pytest.mark.skipif(
    sys.platform not in {"linux", "win32"}, reason="COROS supports Linux and Windows"
)
ACCOUNT = AccountIdentity("coros", "regional-issuer", "opaque-subject")
OTHER = AccountIdentity("coros", "regional-issuer", "another-subject")
VAULT = VaultReference("fit-to-md/coros/connection", "session")
START = datetime(2020, 1, 1, 12, 0, 15, tzinfo=UTC)
T = START + timedelta(days=1)
ZONE = ZoneInfo("UTC")
FIT = Path(__file__).resolve().parents[3] / "fit_files" / "coros_synthetic.fit"
METADATA = Path(".fit-to-md")
DATABASE = METADATA / "coros.sqlite3"


def activity(
    remote_id: str = "a", *, sport: int = 100, start: datetime | None = START
) -> RemoteActivity:
    return RemoteActivity(ActivityIdentity(ACCOUNT, remote_id, sport), start)


@pytest.fixture(autouse=True)
def _portalocker() -> None:
    pytest.importorskip("portalocker")


def test_round_trip_reference_candidates_acquisition_and_conversion(
    tmp_path: Path,
) -> None:
    store = SqliteOutput(tmp_path)
    source = activity()
    options = ConversionOptions(
        workout_report="laps", hr_zone_boundaries=(100, 130, 150, 170)
    )
    other_sport = activity(sport=101, start=None)
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery(
            (source, other_sport),
            discovered_at=T,
            complete=True,
            reference=Reference(T),
            zone=ZONE,
        )
        paths = opened.reserve(source.identity, ZONE)
        opened.registry.record_attempt(source.identity, Stage.ACQUISITION, T)
        opened.registry.record_failure(
            source.identity, Stage.ACQUISITION, FailureReason.UNAVAILABLE
        )
        opened.registry.record_attempt(
            source.identity, Stage.ACQUISITION, T + timedelta(seconds=1)
        )
        receipt = opened.publish_fit(source.identity, FIT.read_bytes())
        opened.registry.record_attempt(source.identity, Stage.CONVERSION, T)
        opened.registry.record_failure(
            source.identity, Stage.CONVERSION, FailureReason.CONVERSION_FAILED
        )
        assert (
            opened.registry.entry(source.identity).tracked.acquisition
            is AcquisitionState.ACQUIRED
        )
        opened.registry.record_attempt(
            source.identity, Stage.CONVERSION, T + timedelta(seconds=1)
        )
        opened.publish_report(source.identity, "# Sortie à vélo\n", options)
        expected = opened.registry.snapshot()

    with store.read(ACCOUNT) as reader:
        assert reader is not None
        assert reader.snapshot() == expected
        assert expected.vault == VAULT
        assert expected.reference == Reference(T)
        assert expected.reference_zone == "UTC"
        assert expected.last_complete_discovery == T
        acquired = reader.entry(source.identity)
        assert acquired.tracked.activity == source
        assert acquired.discovered_at == T
        assert acquired.tracked.acquisition is AcquisitionState.ACQUIRED
        assert acquired.tracked.conversion is ConversionState.CONVERTED
        assert acquired.acquisition_attempts == Attempts(2, T + timedelta(seconds=1))
        assert acquired.conversion_attempts == Attempts(2, T + timedelta(seconds=1))
        assert acquired.conversion_options == options
        assert acquired.fit_integrity == receipt
        assert acquired.paths == paths
        assert reader.entry(other_sport.identity).tracked.activity == other_sport
    assert (tmp_path / paths.fit).read_bytes() == FIT.read_bytes()
    assert (tmp_path / paths.report).read_text(encoding="utf-8") == "# Sortie à vélo\n"
    assert sorted(item.name for item in (tmp_path / "fits").iterdir()) == [
        paths.fit.name
    ]
    (tmp_path / paths.report).write_text("# Édité\n", encoding="utf-8")
    with store.write(ACCOUNT, VAULT) as opened:
        with pytest.raises(OutputConflict):
            opened.publish_report(source.identity, "Upgraded", ConversionOptions())
        assert opened.registry.entry(source.identity) == acquired
    assert (tmp_path / paths.report).read_text(encoding="utf-8") == "# Édité\n"


def test_empty_read_only_and_wrong_account_leave_output_untouched(
    tmp_path: Path,
) -> None:
    old_fit = tmp_path / "old.fit"
    old_fit.write_bytes(b"old manual FIT")
    store = SqliteOutput(tmp_path)
    with store.read(ACCOUNT) as reader:
        assert reader is None
    assert list(tmp_path.iterdir()) == [old_fit]

    with store.write(ACCOUNT, VAULT) as opened:
        assert opened.registry.snapshot().entries == ()
    assert not (tmp_path / "fits").exists()
    metadata = tmp_path / METADATA
    (metadata / "output.lock").unlink()
    original = {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in metadata.iterdir()
    }
    for access in (store.read(OTHER), store.write(OTHER, VAULT)):
        with pytest.raises(OutputAccountMismatch), access:
            pytest.fail("account mismatch was accepted")
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in metadata.iterdir()
    } == original
    assert old_fit.read_bytes() == b"old manual FIT"

    second_output = tmp_path / "another"
    second_output.mkdir()
    with SqliteOutput(second_output).write(OTHER, VAULT) as opened:
        assert opened.registry.snapshot().account == OTHER
        with pytest.raises(OutputAccountMismatch):
            opened.registry.save_discovery(
                (activity(),), discovered_at=T, complete=True
            )
        assert opened.registry.snapshot().last_complete_discovery is None


def test_normal_read_only_does_not_modify_files(tmp_path: Path) -> None:
    store = SqliteOutput(tmp_path)
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((activity(),), discovered_at=T, complete=True)
    metadata = tmp_path / METADATA
    before = {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in metadata.iterdir()
    }
    with store.read(ACCOUNT) as reader:
        assert reader is not None
        assert reader.snapshot().entries
        # The actual SQLite connection enforces read-only too.
        assert isinstance(reader, SqliteRegistry)
        with pytest.raises(StorageFailure, match="Could not update"):
            reader.save_reference(Reference(T), ZONE)
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in metadata.iterdir()
    } == before


def test_reference_and_candidates_rollback_together(tmp_path: Path) -> None:
    store = SqliteOutput(tmp_path)
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_reference(Reference(START), ZONE)
    with closing(sqlite3.connect(tmp_path / DATABASE)) as db, db:
        db.execute(
            """CREATE TRIGGER reject_reference
               BEFORE UPDATE OF reference_utc ON output_account
               WHEN NEW.reference_utc <> OLD.reference_utc
               BEGIN SELECT RAISE(ABORT, 'injected reference failure'); END"""
        )
    with store.write(ACCOUNT, VAULT) as opened:
        with pytest.raises(StorageFailure, match="Could not update"):
            opened.registry.save_discovery(
                (activity(),),
                discovered_at=T,
                complete=True,
                reference=Reference(T),
                zone=ZONE,
            )
        snapshot = opened.registry.snapshot()
        assert snapshot.entries == ()
        assert snapshot.reference == Reference(START)
        assert snapshot.last_complete_discovery is None


def test_malformed_persisted_fit_digest_is_rejected(tmp_path: Path) -> None:
    store = SqliteOutput(tmp_path)
    source = activity()
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((source,), discovered_at=T, complete=True)
        opened.reserve(source.identity, ZONE)
        opened.publish_fit(source.identity, FIT.read_bytes())
    with closing(sqlite3.connect(tmp_path / DATABASE)) as db, db:
        db.execute("UPDATE activities SET fit_sha256 = ?", ("z" * 64,))
    with store.read(ACCOUNT) as reader:
        assert reader is not None
        with pytest.raises(StorageFailure, match="registry is unreadable"):
            reader.snapshot()


def test_discovery_deduplication_and_reference_rules(tmp_path: Path) -> None:
    store = SqliteOutput(tmp_path)
    original = activity()
    changed = activity(start=START + timedelta(hours=1))
    with store.write(ACCOUNT, VAULT) as opened:
        reg = opened.registry
        reg.save_reference(Reference(START), ZONE)
        reg.save_discovery((original, original), discovered_at=T, complete=True)
        reg.save_discovery(
            (changed,), discovered_at=T + timedelta(days=1), complete=False
        )
        with pytest.raises(StorageFailure, match="complete discovery"):
            reg.save_discovery(
                (activity("b"),),
                discovered_at=T,
                complete=False,
                reference=Reference(T),
                zone=ZONE,
            )
        snapshot = reg.snapshot()
        assert snapshot.reference == Reference(START)
        assert snapshot.last_complete_discovery == T
        assert [entry.tracked.activity for entry in snapshot.entries] == [original]


def test_reservation_persists_across_timezones_and_rejects_same_minute(
    tmp_path: Path,
) -> None:
    store = SqliteOutput(tmp_path)
    first = activity()
    second = activity("b", start=START + timedelta(seconds=20))
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((first, second), discovered_at=T, complete=True)
        paths = opened.reserve(first.identity, ZoneInfo("Europe/Paris"))
        assert paths.fit.as_posix() == "fits/2020-01-01 13-00.fit"
        with pytest.raises(OutputConflict, match="reserved by another activity"):
            opened.reserve(second.identity, ZoneInfo("Europe/Paris"))
        assert opened.registry.entry(second.identity).paths is None
        assert not (tmp_path / "fits").exists()
    with store.write(ACCOUNT, VAULT) as opened:
        assert opened.reserve(first.identity, ZONE) == paths


def test_foreign_fit_and_report_are_preserved(tmp_path: Path) -> None:
    store = SqliteOutput(tmp_path)
    first = activity()
    second = activity("b", start=START + timedelta(minutes=1))
    (tmp_path / "fits").mkdir()
    manual_fit = tmp_path / "fits" / "2020-01-01 12-00.fit"
    manual_fit.write_bytes(b"manual")
    report = tmp_path / "2020-01-01 12-01.md"
    report.write_text("manual edits", encoding="utf-8")
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((first, second), discovered_at=T, complete=True)
        with pytest.raises(OutputConflict, match="already exists"):
            opened.reserve(first.identity, ZONE)
        assert opened.registry.entry(first.identity).paths is None
        opened.reserve(second.identity, ZONE)
        opened.publish_fit(second.identity, FIT.read_bytes())
        with pytest.raises(OutputConflict, match="already exists"):
            opened.publish_report(
                second.identity,
                "# Regenerated",
                ConversionOptions(workout_report="laps"),
            )
        assert (
            opened.registry.entry(second.identity).tracked.conversion
            is ConversionState.PENDING
        )
    assert manual_fit.read_bytes() == b"manual"
    assert report.read_text(encoding="utf-8") == "manual edits"


def test_terminal_conversion_preserves_fit_and_never_writes_report(
    tmp_path: Path,
) -> None:
    store = SqliteOutput(tmp_path)
    source = activity()
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((source,), discovered_at=T, complete=True)
        paths = opened.reserve(source.identity, ZONE)
        with pytest.raises(StorageFailure, match="Acquire the COROS FIT"):
            opened.publish_report(source.identity, "premature", ConversionOptions())
        receipt = opened.publish_fit(source.identity, FIT.read_bytes())
        opened.registry.record_attempt(source.identity, Stage.CONVERSION, T)
        opened.registry.record_failure(
            source.identity, Stage.CONVERSION, FailureReason.UNSUPPORTED
        )
        with pytest.raises(StorageFailure, match="unsupported"):
            opened.publish_report(source.identity, "unsupported", ConversionOptions())
        with pytest.raises(StorageFailure, match="current COROS registry state"):
            opened.registry.record_attempt(source.identity, Stage.CONVERSION, T)
        entry = opened.registry.entry(source.identity)
        assert entry.tracked.conversion is ConversionState.UNSUPPORTED
        assert entry.conversion_attempts == Attempts(1, T, FailureReason.UNSUPPORTED)
        assert opened.files.has_fit(paths.fit, receipt)
    assert not (tmp_path / paths.report).exists()


def test_late_collision_never_overwrites_or_records_foreign_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = activity()
    store = SqliteOutput(tmp_path)
    contents = FIT.read_bytes()
    rename = files.rename_new
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((source,), discovered_at=T, complete=True)
        paths = opened.reserve(source.identity, ZONE)

        def late_file(temporary: Path, destination: Path) -> None:
            assert temporary.parent == destination.parent
            assert temporary.read_bytes() == contents
            destination.write_bytes(b"late external file")
            rename(temporary, destination)

        monkeypatch.setattr(files, "rename_new", late_file)
        with pytest.raises(OutputConflict, match="already exists"):
            opened.publish_fit(source.identity, contents)
        entry = opened.registry.entry(source.identity)
        assert entry.fit_integrity is None
        assert entry.tracked.acquisition is AcquisitionState.PENDING
    final = tmp_path / paths.fit
    assert final.read_bytes() == b"late external file"
    assert list(final.parent.iterdir()) == [final]


def test_late_report_collision_discards_digest_even_when_bytes_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = activity()
    markdown = "# Sortie à vélo\n"
    contents = markdown.encode("utf-8")
    rename = files.rename_new

    def late_file(temporary: Path, destination: Path) -> None:
        assert temporary.read_bytes() == contents
        destination.write_bytes(contents)
        rename(temporary, destination)

    with SqliteOutput(tmp_path).write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((source,), discovered_at=T, complete=True)
        paths = opened.reserve(source.identity, ZONE)
        opened.publish_fit(source.identity, FIT.read_bytes())
        monkeypatch.setattr(files, "rename_new", late_file)
        with pytest.raises(OutputConflict):
            opened.publish_report(source.identity, markdown, ConversionOptions())

        entry = opened.registry.entry(source.identity)
        assert entry.tracked.conversion is ConversionState.PENDING
        assert entry.report_sha256 is None
        assert not opened.recover_report(entry)
    assert (tmp_path / paths.report).read_bytes() == contents
    assert not list(tmp_path.glob(".fit-to-md-*.tmp"))


def test_interruption_after_fit_rename_leaves_evidence_not_acquired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteOutput(tmp_path)
    source = activity()

    def interrupted(self: SqliteRegistry, identity: ActivityIdentity) -> None:
        raise KeyboardInterrupt

    with (
        monkeypatch.context() as patch,
        pytest.raises(KeyboardInterrupt),
        store.write(ACCOUNT, VAULT) as opened,
    ):
        opened.registry.save_discovery((source,), discovered_at=T, complete=True)
        paths = opened.reserve(source.identity, ZONE)
        patch.setattr(SqliteRegistry, "record_acquired", interrupted)
        opened.publish_fit(source.identity, FIT.read_bytes())

    with store.write(ACCOUNT, VAULT) as opened:
        entry = opened.registry.entry(source.identity)
        assert entry.tracked.acquisition is AcquisitionState.PENDING
        assert entry.fit_integrity is not None
        assert opened.files.has_fit(paths.fit, entry.fit_integrity)
        final = tmp_path / paths.fit
        assert list(final.parent.iterdir()) == [final]
        final.write_bytes(b"x" * entry.fit_integrity.size)
        assert not opened.files.has_fit(paths.fit, entry.fit_integrity)
        final.unlink()
        assert not opened.files.has_fit(paths.fit, entry.fit_integrity)


def test_interruption_before_fit_rename_keeps_candidate_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqliteOutput(tmp_path)
    source = activity()

    def interrupted(temporary: Path, destination: Path) -> None:
        raise KeyboardInterrupt

    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((source,), discovered_at=T, complete=True)
        paths = opened.reserve(source.identity, ZONE)
        with monkeypatch.context() as patch, pytest.raises(KeyboardInterrupt):
            patch.setattr(files, "rename_new", interrupted)
            opened.publish_fit(source.identity, FIT.read_bytes())
        entry = opened.registry.entry(source.identity)
        assert entry.fit_integrity is not None
        assert entry.tracked.acquisition is AcquisitionState.PENDING
        assert not opened.files.has_fit(paths.fit, entry.fit_integrity)
        assert list((tmp_path / "fits").iterdir()) == []
    with store.write(ACCOUNT, VAULT) as opened:
        assert (
            opened.publish_fit(source.identity, FIT.read_bytes()) == entry.fit_integrity
        )
        assert (
            opened.registry.entry(source.identity).tracked.acquisition
            is AcquisitionState.ACQUIRED
        )


def test_expected_existing_output_and_managed_path_types(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(StorageFailure, match="existing directory"):
        SqliteOutput(missing)
    assert not missing.exists()
    (tmp_path / METADATA).touch()
    with pytest.raises(StorageFailure, match="unexpected file type"):
        with SqliteOutput(tmp_path).write(ACCOUNT, VAULT):
            pytest.fail("file accepted as metadata directory")


def test_missing_portalocker_is_a_safe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(name: str) -> None:
        assert name == "portalocker"
        raise ImportError

    monkeypatch.setattr(output, "import_module", missing)
    with pytest.raises(StorageFailure, match=r"Install the \[coros\] extra"):
        with SqliteOutput(tmp_path).write(ACCOUNT, VAULT):
            pytest.fail("lock bypassed")


def test_process_contention_dry_run_and_release_after_forced_exit(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "output"
    destination.mkdir()
    ready = tmp_path / "ready"
    command = [
        sys.executable,
        "-c",
        """
import sys
from pathlib import Path
from time import sleep

from fit_to_md.application.synchronization_storage import VaultReference
from fit_to_md.domain.synchronization.model import AccountIdentity
from fit_to_md.infrastructure.synchronization.output import SqliteOutput

with SqliteOutput(Path(sys.argv[1])).write(
    AccountIdentity("coros", "regional-issuer", "opaque-subject"),
    VaultReference("fit-to-md/coros/connection", "session"),
):
    Path(sys.argv[2]).touch()
    sleep(60)
""",
        str(destination),
        str(ready),
    ]
    source_root = Path(__file__).resolve().parents[4] / "src"
    with subprocess.Popen(
        command, env={**environ, "PYTHONPATH": str(source_root)}
    ) as holder:
        try:
            deadline = monotonic() + 10
            while (
                not ready.exists() and holder.poll() is None and monotonic() < deadline
            ):
                sleep(0.02)
            assert ready.exists(), f"output lock holder did not start ({holder.poll()})"
            store = SqliteOutput(destination)
            start = monotonic()
            for access in (store.read(ACCOUNT), store.write(ACCOUNT, VAULT)):
                with pytest.raises(OutputBusy, match="Another synchronization"), access:
                    pytest.fail("lock already held by child")
            assert monotonic() - start < 3
        finally:
            holder.kill()
            holder.wait(timeout=5)
    assert (destination / METADATA / "output.lock").is_file()
    with store.read(ACCOUNT) as reader:
        assert reader is not None
        assert reader.snapshot().account == ACCOUNT
    with store.write(ACCOUNT, VAULT) as opened:
        assert opened.registry.snapshot().entries == ()


@pytest.mark.parametrize("damage", ["unknown-version", "corrupt", "missing", "wal"])
def test_unusable_registry_is_never_reinitialized(tmp_path: Path, damage: str) -> None:
    store = SqliteOutput(tmp_path)
    with store.write(ACCOUNT, VAULT):
        pass
    database = tmp_path / DATABASE
    if damage == "unknown-version":
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE schema_version SET version = 999")
        message = "Unsupported COROS output registry schema version"
    elif damage == "missing":
        database.unlink()
        message = "registry is missing"
    elif damage == "wal":
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        message = "registry is unreadable"
    else:
        database.write_bytes(b"not a database")
        message = "registry is unreadable"

    metadata = tmp_path / METADATA
    before = {p.name: p.read_bytes() for p in metadata.iterdir()}
    for access in (store.read(ACCOUNT), store.write(ACCOUNT, VAULT)):
        with pytest.raises(StorageFailure, match=message), access:
            pytest.fail("unusable registry was reopened")
    assert {p.name: p.read_bytes() for p in metadata.iterdir()} == before


@pytest.mark.parametrize("interrupted", [False, True])
def test_migration_backs_up_original_and_rolls_back_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    from fit_to_md.infrastructure.synchronization import registry

    version = registry._VERSION
    store = SqliteOutput(tmp_path)
    source = activity()
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery((source,), discovered_at=T, complete=True)
        original = opened.registry.snapshot()

    database = tmp_path / DATABASE

    def migrate(connection: sqlite3.Connection) -> None:
        assert len(list(database.parent.glob("*.bak"))) == 1
        connection.execute("ALTER TABLE activities ADD COLUMN migration_test TEXT")
        if interrupted:
            raise KeyboardInterrupt

    monkeypatch.setattr(registry, "_VERSION", version + 1)
    monkeypatch.setitem(registry._MIGRATIONS, version, migrate)

    before = {p.name: p.read_bytes() for p in database.parent.iterdir()}
    with pytest.raises(StorageFailure, match="writable migration"), store.read(ACCOUNT):
        pytest.fail("dry-run accepted a registry requiring migration")
    assert {p.name: p.read_bytes() for p in database.parent.iterdir()} == before

    if interrupted:
        with pytest.raises(KeyboardInterrupt), store.write(ACCOUNT, VAULT):
            pytest.fail("interrupted migration was committed")
    else:
        with store.write(ACCOUNT, VAULT) as opened:
            assert opened.registry.snapshot() == original

    with closing(sqlite3.connect(database)) as db:
        expected = version if interrupted else version + 1
        assert db.execute("SELECT version FROM schema_version").fetchone() == (
            expected,
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(activities)")}
        assert ("migration_test" in columns) is not interrupted

    restored = tmp_path / "restored"
    (restored / METADATA).mkdir(parents=True)
    (restored / DATABASE).write_bytes(next(database.parent.glob("*.bak")).read_bytes())
    monkeypatch.setattr(registry, "_VERSION", version)
    with SqliteOutput(restored).read(ACCOUNT) as reader:
        assert reader is not None
        assert reader.snapshot() == original
    assert not (restored / METADATA / "output.lock").exists()


def test_registry_reads_options_from_before_coros_report_conversion(
    tmp_path: Path,
) -> None:
    store = SqliteOutput(tmp_path)
    source = activity()
    with store.write(ACCOUNT, VAULT) as opened:
        opened.registry.save_discovery([source], discovered_at=T, complete=True)
        opened.reserve(source.identity, ZONE)
        opened.publish_fit(source.identity, FIT.read_bytes())
        opened.publish_report(source.identity, "# Report", ConversionOptions())

    legacy_options = """{
      "workout_report": "off",
      "hr_zone_boundaries": null,
      "weather_mode": "fit",
      "elevation_source": "fit",
      "elevation_smoothing_distance": 170.0,
      "elevation_min_change": 0.4,
      "dem_sample_distance": 30.0
    }"""
    with closing(sqlite3.connect(tmp_path / DATABASE)) as connection, connection:
        connection.execute(
            "UPDATE activities SET conversion_options = ?", (legacy_options,)
        )

    with store.read(ACCOUNT) as reader:
        assert reader is not None
        assert reader.entry(source.identity).conversion_options == ConversionOptions()
