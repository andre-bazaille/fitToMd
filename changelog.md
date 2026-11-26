## 2026-11-26

- Fixed Windows COROS login for sessions larger than one Credential Manager
  entry. The Windows vault stages the new session before replacing the current
  one and removes all session parts on logout.
- Recorded the Windows offline tests, live COROS login, session reuse and
  renewal on Linux and Windows, and the Training Hub comparison for one FIT
  file in the L9 checklist.

## 2026-11-24

- Configured pytest to import `fit_to_md` from the working tree and added a
  regression test that checks the package source path.

## 2026-11-22

- Translated the [COROS sync guide](docs/coros-sync-guide.md) into English.
- Recorded five findings from the [COROS synchronization code review](docs/coros-sync-code-review.md), ordered by severity.
- Fixed COROS downloads with large FIT responses and made interrupted quota
  initialization resumable when the existing SQLite registry is verified.
- Stopped catalogue discovery when COROS requires a delay, aligned preview FIT
  estimates with acquisition order, and added daily catalogue and FIT counts
  to the synchronization summary.
- Added Markdown generation to normal COROS synchronization, with the existing
  report settings and CLI precedence over `--config`. Verified FITs are reused
  after download-only runs, missing reports are regenerated, and local conversion
  continues when the FIT quota is reached. Acquisition and conversion have
  separate outcomes and counters. Existing reports are preserved and unsupported
  FITs keep a terminal conversion state.
- Extended `coros status` with optional output paths, reference, timezone and
  pending work from the read-only registry.
- Added the [COROS operations guide](docs/coros-sync-guide.md) for Linux and
  Windows setup, synchronization, diagnostics, and backup and recovery. Updated
  the L9 qualification record with offline evidence and remaining checks.

## 2026-11-21

- Added incremental COROS FIT downloads and `--init-coros-reference`.
  Complete discovery commits the candidates and UTC reference together.
  Incremental runs resume tracked downloads outside the discovery window,
  reconcile verified FITs after interruption, and reacquire missing or
  damaged FITs. Damaged files are preserved in `.fit-to-md/damaged-fits/`.
- Added `fit-to-md coros sync --download-only` with `--since` or `--last-days`.
  It records discovered activities, reuses verified FITs and saves new FITs
  atomically under the account's shared quota. The summary reports incomplete
  discovery, individual failures and downloads deferred for quota.
- Added `fit-to-md coros sync --dry-run` for explicit and incremental
  previews. The command checks catalogue coverage, subdivides saturated
  periods and reports known activities, new work, retries, conflicts and
  estimated FIT requests without changing local tracking or requesting FIT
  files or URLs.
- Added `fit-to-md coros init-quota` and a persistent FIT budget shared by all
  outputs for one account. Reservations enforce 50 requests per rolling UTC
  24 hours and survive timeouts and logout. The ledger reports catalogue and FIT
  calls separately by local day. FIT HTTP 429 responses surface an optional
  retry time and stop further acquisition requests for the run.
- Added COROS output storage with an account-bound SQLite registry, versioned
  migrations, and a nonblocking per-output lock. FIT and report publication
  preserves existing files, and the registry records FIT integrity for recovery.
  Dry-run access leaves the output untouched and refuses a busy directory.

## 2026-11-17

- Added COROS production `login`, local `status` and `logout`. The connection
  confirms the regional account, stores credentials in the OS vault and saves
  token rotation before non-interactive reuse. The experimental probe keeps its
  own session.
- Added the Synchronization domain for COROS L1, with account-scoped identities,
  calendar periods, independent acquisition and conversion states, and offline
  planning of new work, retries and UTC reference updates.
- Added offline-tested COROS identity and activity-date diagnostics to the
  experimental probe, with opaque fingerprints, trusted userinfo discovery,
  UTC/local DST-aware timestamps and explicit rejection of incomplete dates.
- Documented [L0 technical decisions](docs/coros-sync-technical-decisions.md),
  port boundaries, quota recovery, manual conversion policy and remaining live
  qualification gates, including reported Linux/Europe identity stability after
  refresh/reconnection and UTC/local date concordance; midnight and cross-account
  qualification remain open, with J0 pending.
- Added a [COROS synchronization implementation and tracking plan](docs/coros-sync-implementation-plan.md), with delivery batches, dependencies, decision gates, and acceptance-test coverage.

## 2026-11-16

- Added the [functional specification for COROS FIT synchronization](docs/coros-sync-functional-specification.md), covering activity selection, authentication, recovery, and validation criteria.

## 2026-11-08

- Added a reproducible synthetic COROS FIT fixture and offline CLI integration
  tests covering track GPS fallback, short event data recovery, weather config,
  recent/archive weather lookup, provider failure, and omitted device temperature.
- Removed device temperature from Markdown reports; weather continues to use
  explicit FIT observations or optional historical lookup.
- Fixed automatic weather lookup for COROS files without session GPS coordinates
  by falling back to a valid track coordinate pair. Recent activities now use
  Open-Meteo's recent-past endpoint during the historical archive delay.
- Quieted the recoverable one-byte COROS event data definition warning while
  preserving warnings for CRC failures and other malformed fields.
- Separated FIT device temperature measurements from ambient weather. Weather now
  uses current FIT weather observations nearest the activity start, or opt-in
  historical lookup even when device temperatures exist. COROS sensor averages
  no longer appear as weather or prevent weather enrichment.

## 2026-11-01

- Preserved FIT session ascent/descent totals, with lap and filtered-record
  fallbacks, instead of overriding watch totals with smoothed altitude. DEM
  replacement still recalculates totals from the enriched profile.

## 2026-09-24

- Added weekly Dependabot checks for Python dependencies and GitHub Actions.
- Licensed the project under the MIT License and declared the license in package metadata.
- Accepted timer-derived active intervals when a FIT session is saved after its
  timer stops, restoring session and per-lap heart-rate zone durations while
  retaining checks for inconsistent timer and elapsed totals.

## 2026-09-23

- Added opt-in workout-aware reports with native laps, explicitly linked
  repetitions, recovery heart-rate changes, and configurable five-zone heart-rate
  coverage. New CLI and config options work for single files and directories;
  default reports remain unchanged.
- Preserved missing FIT sample boundaries in workout coverage and fixed plain
  `pytest` test collection. Synthetic binary FIT acceptance tests and the full
  CI suite pass (558 tests).

## 2026-09-22

- Added normalized FIT workout metadata and active-time evidence, with
  conservative handling of missing or ambiguous lap-to-step links. Documented
  the feature plan and specification and established regression fixtures.

## 2026-09-21

- Included the final partial-distance segment in kilometric splits and heart-rate
  dynamics, with its actual distance, normalized per-kilometer pace, complete
  boundary sampling, and cautious terminal-lap fallback. Partial timing now
  requires the matching lap to be terminal, and rejected near-kilometer laps no
  longer corrupt the cumulative-distance validation.

## 2026-09-17

- Allowed directory inputs to use `--output` with an existing destination
  directory, including source-name and activity-time report naming.
- Preserved report generation across interrupted weather and elevation HTTP
  responses, classified HTTP 429 responses as quota exhaustion, and distinguished
  malformed nested provider data from legitimate missing coverage while retaining
  valid partial values.
- Moved directory output planning and execution into an application batch use
  case with typed per-file outcomes and a Markdown writer port; existing and
  colliding activity-time outputs now avoid enrichment and rendering, while
  accepted reports are generated and written incrementally.
- Added typed activity-read failures and provider lookup results, preserving
  partial enrichment values with diagnostics for unavailable services, invalid
  responses, quota exhaustion, and missing coverage; degraded reports now warn
  on stderr while completing successfully.
- Replaced the report-producing FIT extractor with an `ActivityReader` boundary;
  the application now owns optional enrichment, report assembly, and rendering,
  while decoder-independent DEM profile rules live in the Reporting domain.
- Replaced private generator/extractor inspection for elevation progress and
  usage reporting with an explicit typed diagnostics handle, structured run
  statistics, and CLI-owned user-facing formatting.
- Strengthening enforcement across domain and application modules, including relative imports, external
  domain dependencies, and the permitted bounded-context dependency direction.
- Expanded the architecture guide with the Privacy context, report and sanitizer
  composition roots, current CLI-owned batch workflow, permitted context
  dependencies, and a separate description of proposed application ownership.

## 2026-09-16

- Preserved active durations from exactly aligned kilometer laps when record
  timing falls back to wall time; dynamics now disclose unknown pause timing and
  show boundary samples instead of assigning misleading intermediate times.
- Required contiguous DEM coverage before replacing altitude, retaining FIT
  altitude and grade for isolated terrain samples and preventing artificial
  ascent/descent from single-point source changes.
- Corrected missing session start times to prefer the earliest activity record,
  then derive from the session finish and elapsed duration before using the finish
  as a last resort.
- Preserved the canonical parent sport in report summaries so running sub-sports,
  including treadmill, track, and trail running, consistently render pace rather
  than speed in session summaries and dynamics.
- Made activity-time output filenames portable across Windows and POSIX by using
  `YYYY-MM-DD HH-MM.md`, while continuing to recognize legacy colon-named reports
  during directory processing.
- Made optional Open-Meteo and OpenTopoData enrichment fail gracefully when a
  service returns malformed JSON structures or non-finite numeric values.
- Rejected NaN and infinite elevation settings at both CLI/config parsing and
  programmatic construction boundaries, preventing conversion-time numeric errors.
- Preserved FIT-native session, record, and lap temperatures in automatic weather
  mode, avoiding unnecessary historical lookups and replacement weather summaries.
- Preserved FIT altitude across missing DEM samples and failed elevation request
  batches instead of interpolating invented terrain values through coverage gaps.
- Corrected kilometer splits and dynamics to use the same record-derived distance
  boundaries, preventing warm-up, recovery, manual, and sub-kilometer laps from
  being mislabeled as absolute kilometers while retaining aligned lap-only fallback.
- Rejected multi-session FIT activities with a clear unsupported-input error
  instead of combining session summaries with laps, records, and timer events from
  different sessions.
- Prevented report output paths from overwriting their source FIT file, including
  relative or absolute path aliases, symbolic links, and hard links.
- Added `--output-by-activity-time` to name generated Markdown files from the
  activity start time using the `YYYY-MM-DD HH:MM` format.
- Added directory batch processing for direct-child FIT files, including
  automatic output matching, existing-report skipping, and activity-time
  naming support.

## 2026-09-02

- Reworked the public README with source installation and usage guidance,
  detailed OpenTopoData dataset, server, privacy, limits, and route-sampling
  documentation, and an explanation of elevation smoothing and tuning.
- Sanitized the real FIT test fixtures, renamed them with generic identifiers,
  removed private metadata, and shifted activity timestamps to the deterministic
  public-fixture date.
- Added `fit-sanitize`, a DDD-structured utility that uses an explicit message
  allowlist to retain only core activity data, removes serial-number,
  developer, and unknown fields, and shifts explicit and compressed activity
  timestamps to a deterministic public-fixture date while preserving the
  original GPS track. Custom policies that retain developer fields now preserve
  their metadata, definitions, and values consistently.
- Reused module-scoped decoded FIT fixtures across real-file integration tests so each source file is decoded only once per test session.
- Raised the minimum supported Python version to 3.12 and modernized type annotations accordingly.
- Added Ruff linting and formatting, strict mypy checks, branch-aware coverage with an 80% minimum, and a GitHub Actions quality job.
- Added an executable `ci.sh` helper that runs the complete CI quality suite locally.
- Added `--config` support for reusable CLI defaults stored as `option = value` pairs, with command-line options taking precedence.
- Added validation and unit coverage for missing, malformed, unknown, duplicate, and invalid configuration values.

## 2026-08-27

- Restored Python 3.9 and 3.10 compatibility by replacing `datetime.UTC` with `timezone.utc`.
- Made external weather and DEM enrichment opt-in so the default CLI execution keeps activity location data local.
- Aligned programmatic generator defaults with the CLI defaults.
- Made DEM-corrected reports derive split elevation from enriched records instead of retaining FIT-native lap elevation.
- Added regression coverage for offline defaults and DEM elevation consistency when kilometer laps are present.
- Removed the obsolete `--transition-window` option and aligned the dynamics documentation with per-kilometer sampling.
- Added friendly CLI errors for directory inputs, invalid FIT data, input read failures, and Markdown write failures.
- Moved the normalized activity model and report calculation services from the fitdecode adapter into the domain layer, retaining compatibility re-exports for existing programmatic imports.
- Replaced raw FIT session dictionaries in the domain with a typed `ActivitySession` translated by the fitdecode adapter.
- Added direct domain-service tests and an automated dependency rule preventing domain imports from application or infrastructure.
- Added an architecture guide and refreshed installation, output, exit-code, report-format, and external-enrichment documentation.

## 2026-04-13

- Changed the CLI default output behavior so omitting `--output` now writes a Markdown file next to the FIT input, using the same base name with a `.md` suffix, while still echoing the report to stdout.

## 2026-03-29

- Excluded paused time from record-derived kilometer splits and per-kilometer heart-rate dynamics by mapping records onto FIT timer start/stop events instead of raw wall-clock timestamps.
- Added regression coverage for paused activities with both synthetic timer-event fixtures and the new real FIT file fixture.
- Enforced OpenTopoData public API limits in the elevation adapter, including 100 locations per request, 1 request per second, and a stateless per-run cap of 1000 requests.
- Added CLI reporting for OpenTopoData request usage at the end of each run without persisting cross-run daily counters.
- Added OpenTopoData request progress reporting on stderr while DEM batches are being fetched in `dem` and `hybrid` modes.
- Added CLI options for OpenTopoData dataset and base URL so DEM lookups can target the public service or a self-hosted instance without code changes.
- Added a hybrid elevation mode that keeps FIT altitude when the trace looks stable and falls back to DEM elevation when the FIT altitude is clearly noisy.
- Added a DEM elevation provider abstraction and an initial OpenTopoData-backed implementation so elevation can be sourced from terrain data instead of FIT altitude.
- Added route resampling for DEM lookup with a new CLI parameter to control the sample spacing, defaulting to 30 meters.
- Wired a new CLI elevation source switch so reports can use either FIT-native altitude or DEM-derived altitude.
- Added unit coverage for OpenTopoData request parsing and DEM-based elevation replacement in the FIT extractor.
- Switched Heart Rate Dynamics from lap-based transitions to per-kilometer elapsed time series so each completed kilometer reports HR/pace/grade from `0:00` to the split finish, with a configurable CLI step size that defaults to 15 seconds.
- Replaced noisy record-to-record transition grade synthesis with a smoothed altitude-based grade estimate that reuses the elevation smoothing model and still omits grade when movement is paused or distance context is insufficient.
- Changed running speed presentation in Markdown reports from km/h to pace per kilometer, including average summary pace and transition samples.
- Added CLI flags to tune elevation smoothing distance and minimum elevation change so session gain/loss can be calibrated per device or file set.
- Replaced raw FIT session ascent/descent totals with a filtered record-derived elevation calculation to reduce barometric noise in session summaries.
- Added unit and integration coverage for noise-resistant elevation gain/loss handling.
- Initialized the Python project scaffold with a DDD-aligned `src` layout.
- Added a CLI entrypoint, domain/application/infrastructure boundaries, and a Markdown renderer.
- Added unit test scaffolding for the use case, CLI flow, and Markdown rendering.
- Implemented FIT decoding for session, lap, and record messages with derived kilometer splits and lap transition samples.
- Refactored Markdown output into composable section renderers so new report sections can be added independently.
- Added extractor tests covering record-driven splits, lap fallback behavior, and transition sampling.
- Added CLI flags to configure transition sample interval and transition window without code changes.
- Added integration tests that exercise the real FIT fixtures through the extractor, renderer, and CLI.
- Normalized running cadence from FIT running-cadence fields to full steps per minute and suppressed bogus computed grades during paused transition samples.
- Added temperature-based weather fields to the session summary with FIT extraction fallbacks from session, lap, and record messages.
- Added optional historical weather enrichment in CLI `auto` mode so reports can include conditions and wind when Garmin Connect-style weather is not embedded in the FIT file.
