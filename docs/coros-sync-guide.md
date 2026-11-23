# Sync COROS activities with fitToMd

## Installation and first sync

From the repository root, use Python 3.12 or later and run
`python -m pip install '.[coros]'`. The extra installs the dependencies for
locking and time zone support, plus `SecretStorage` on Linux and `keyring` and
`tzdata` on Windows. On Linux, your session's Secret Service must be available
and unlocked. On Windows, the command uses Credential Manager under your
Windows account. Have a browser available for the first authorization and
choose a local output directory on a volume that supports publishing files
atomically without overwriting existing files.

**Linux (Bash)**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install '.[coros]'
mkdir -p reports
fit-to-md coros login --region eu
fit-to-md coros sync --dry-run --last-days 7 --output reports/
fit-to-md coros init-quota
fit-to-md coros sync --last-days 7 --output reports/
fit-to-md coros status --output reports/
```

**Windows (PowerShell)**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install '.[coros]'
New-Item -ItemType Directory -Force -Path reports
fit-to-md coros login --region eu
fit-to-md coros sync --dry-run --last-days 7 --output .\reports\
fit-to-md coros init-quota
fit-to-md coros sync --last-days 7 --output .\reports\
fit-to-md coros status --output .\reports\
```

Choose your account region from `eu`, `us`, and `cn`. The first login defaults
to `eu`. Subsequent logins default to the saved region. Run
`fit-to-md coros logout` before switching accounts or regions. Logging out
removes the local session and preserves files, tracking data, and the quota
binding. Each output directory is tied to one account. Use a separate
directory for each account.

`status` checks the local session without contacting COROS. With `--output`,
it also shows the paths, the UTC reference, its saved time zone, and pending
work. The `init-quota` command in the example assumes the quota has never been
initialized. If this installation already has a quota, reuse it or follow the
[quota recovery procedure](#restore-the-quota).

## Date ranges and resuming sync

| Command | Usage |
| --- | --- |
| `fit-to-md coros sync --since 2026-11-01 --output reports/` | From midnight on the specified date in the local time zone up to, but excluding, the time the command starts. |
| `fit-to-md coros sync --last-days 7 --output reports/` | Seven calendar days, including today, up to, but excluding, the time the command starts. |
| `fit-to-md coros sync --output reports/` | Resume pending work and discover activities from the start of the local day containing the UTC reference. |
| `fit-to-md coros sync --download-only --last-days 7 --output reports/` | Save FIT files in `fits/` and defer Markdown report generation. The next regular sync reuses verified FIT files. |
| `fit-to-md coros sync --init-coros-reference 2026-11-01 --output reports/` | Set the reference explicitly while preserving the quota, files, and tracking data. Run this operation on its own, without a date range, preview, or download-only option. |

The first `sync` requires `--since` or `--last-days` unless you have already
set the reference. After initialization, use an explicit date range to pick up
a late import whose start date falls before the incremental sync window.
Searching an explicit range preserves the existing reference. An incremental
sync advances it once discovery completes, even if downloads remain pending.

`sync` accepts `--config FILE` for the `output` directory and report settings
such as `workout-report`, `hr-zone-boundaries`, `weather-mode`, and
`elevation-source`. CLI options take precedence. Always specify explicit date
ranges on the command line. `status --config FILE` uses the `output` setting.

Add `--dry-run` to view the plan and the estimated number of FIT requests.
A preview can refresh the session and query the COROS activity catalog. It
leaves the output directory and quota ledger untouched and makes no requests
for FIT files or download URLs. The COROS catalog limits the number of
results per response. Sync splits date ranges that hit the limit and
deduplicates activities. If a single day still hits the limit or the catalog
results are incomplete, the plan is provisional. An actual sync keeps
candidates with known dates and pauses downloads and conversion. The
reference stays where it is or, on the first run, is set to the start of the
date range. How COROS handles midnight boundaries still needs verification
with Training Hub.

Sync preserves existing reports it tracks, including any manual edits. If you
delete a report, sync recreates it from the verified FIT file. An unrelated
file at the expected path causes a conflict. Check the file's contents, move
it, and rerun the command. If an activity is marked `conversion_unsupported`,
sync keeps the FIT file and treats the conversion failure as permanent. You
can try converting the file locally with `fit-to-md path/to/activity.fit`.

## Quota and diagnostics

The local limit is 50 individual FIT requests in a rolling 24-hour UTC window.
All output directories for the same account on this installation share the
counter. A request still counts if its response is lost. The daily count in
the summary uses the local time zone, while the limit uses the rolling window.
The local counter excludes requests from other clients or machines. If COROS
rejects a request because of its quota, that rejection takes precedence and
the command shows when to retry if the time is known.

When the local limit is reached or COROS rejects a FIT request because of its
quota, sync keeps the candidates and continues converting available verified
FIT files. Rerun the command after the indicated time to resume downloads.
The next run retries failed FIT requests.

| Code | Meaning |
| --- | --- |
| `0` | The command succeeded. A completed preview can still report conflicts. |
| `1` | Partial result or operational error, such as incomplete catalog results, a quota, session, lock, or storage issue, or a conflict during the run. |
| `2` | Invalid arguments or configuration, an unspecified or nonexistent output directory, or a missing initial reference. |
| `130` | The user interrupted the command. |

The command writes the text summary to stdout and progress and diagnostics
to stderr. If the session is missing or has been revoked, explicitly rerun
`fit-to-md coros login`. If the credential store is inaccessible, check that
you installed the `[coros]` extra and that your system session can access the
credential store. If the lock is held, wait for the other command to finish.
If the volume does not support atomic file publication, restore the entire
directory to a compatible volume.

## Back up and restore the output directory

1. Stop all fitToMd commands that use this directory. Copy the entire
   directory, including `fits/`, the Markdown reports, and the hidden
   `.fit-to-md/` directory. It contains `coros.sqlite3`, the initialization
   tracking data, the lock file, and any archived damaged FIT files.
2. Restore the full copy to a separate directory without merging files from
   multiple backups. On another machine, install the `[coros]` extra and log in
   to the same COROS account and region. Session credentials remain in the
   original machine's credential store.
3. Run `fit-to-md coros status --output RESTORED_PATH`. Check the UTC reference,
   time zone, pending work, and expected files. Before running an actual
   `sync`, also check the quota available on this installation.

If the registry is lost or corrupted, restore it from a consistent backup.
If you have no backup, a new output directory starts with fresh tracking data
and may request files you previously downloaded. The shared quota still
applies.

fitToMd applies supported migrations when it opens the database for writing.
It first backs up the database to `.fit-to-md/coros.sqlite3.<identifier>.bak`,
then applies the migration in a transaction. `status` and `--dry-run` report
when a migration requires write access. If migration fails or the database
version is unknown, leave the directory as it is and check the fitToMd version
or restore a compatible backup before resuming.

### Restore the quota

The quota has its own SQLite database and a separate account binding in the
credential store. On Linux, the first initialization creates the database
under `$XDG_STATE_HOME/fit-to-md/coros/` if the XDG path is absolute, or under
`~/.local/state/fit-to-md/coros/` otherwise. On Windows, it creates the database
under `%LOCALAPPDATA%\fit-to-md\coros\`. The binding contains the absolute path
and the identity of the ledger.

Before backing up or restoring the quota, stop all COROS operations for the
account, including those using other output directories. To restore the
quota, put a trusted copy of the database back at the recorded path. If needed,
restore the matching binding from the system credential store backup. An old
copy that omits FIT requests still within the rolling window can undercount
usage. The CLI has no command to transfer or reset the quota. If you do not
have a trusted database and its matching binding, including after moving to
another machine, keep downloads paused. Use `init-quota` only if you know this
is the first initialization. If needed, convert the FIT files you have already
restored with the local conversion command.
