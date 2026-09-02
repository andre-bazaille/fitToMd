# fitToMd

`fitToMd` is a Python CLI project for converting Garmin FIT files into structured Markdown reports suitable for LLM-based coaching analysis.

Python 3.12 or newer is required.

## Project Layout

- `src/fit_to_md/domain/activity/`: decoder-independent activity model.
- `src/fit_to_md/domain/reporting/`: report entities, ports, and calculation services.
- `src/fit_to_md/application/`: use-case orchestration.
- `src/fit_to_md/infrastructure/`: FIT, Markdown, weather, and elevation adapters.
- `tests/`: domain, application, infrastructure, architecture, and integration tests.

The domain has no dependency on application or infrastructure code. See [the architecture guide](docs/architecture.md) for the bounded contexts and dependency rules.

## Quick Start

### Linux and macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
python -m fit_to_md --help
python -m fit_to_md tests/fit_files/0001.fit --dynamics-step-size 5
```

### Preparing a public FIT fixture

Real FIT files contain personal profile, device, and timestamp metadata. Create
a sanitized copy before committing one as a public test fixture:

```bash
fit-sanitize private-activity.fit --output activity.public.fit
```

The command retains only the FIT messages required to represent the activity:
file identity, activity, session, sport, lap, record, and event messages. All
other messages are removed by default, including known Garmin profile, device,
health, workout, and course data as well as messages unknown to the installed
FIT profile. Serial numbers, developer fields, and unknown fields are also
removed. The activity timeline is shifted to `2020-01-01T12:00:00Z` by default
while preserving durations and the complete GPS track. Use `--start-at` to
choose a different synthetic ISO 8601 timestamp. The GPS track remains
identifying and must only be retained deliberately. The output path is
mandatory so a private timestamp embedded in the source filename is not reused
accidentally.

## Quality Checks

Install the development dependencies, then run the same checks enforced by CI:

```bash
python -m pip install -e '.[dev]'
./ci.sh
```

The script runs Ruff linting and formatting checks, strict mypy validation, and
pytest with coverage. It uses `.venv/bin/python` automatically when available;
set `FIT_TO_MD_PYTHON` to select another Python executable.

The test command measures statement and branch coverage for `fit_to_md` and fails
when total coverage is below 80%.

### Configuration File

Use `--config PATH` to load reusable defaults from a small text file. Each line uses
`option = value` without the leading `--`; blank lines and comments starting with `#`
are ignored. Underscores in option names are also accepted.

```ini
# .config
dynamics-step-size = 5
weather-mode = fit
elevation-source = hybrid
dem-sample-distance = 30
```

```bash
python -m fit_to_md tests/fit_files/0001.fit --config ./.config
```

An option passed directly on the command line overrides the same option in the file.
The configurable options are `output`, `dynamics-step-size`, `weather-mode`,
`elevation-smoothing-distance`, `elevation-min-change`, `elevation-source`,
`dem-sample-distance`, `opentopodata-dataset`, and `opentopodata-base-url`.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
python -m fit_to_md --help
python -m fit_to_md .\tests\fit_files\0001.fit --dynamics-step-size 5
```

## Output and Exit Codes

By default, the CLI writes the report next to the input using the same name with a `.md` extension and also prints it to stdout. Use `--output PATH` to choose another file. An existing output file is replaced.

- `0`: report generated and written successfully.
- `1`: invalid FIT data, provider failure, read failure, or write failure.
- `2`: invalid command-line arguments or missing/non-file input path.

## Common Options

```powershell
python -m fit_to_md .\tests\fit_files\0001.fit --elevation-smoothing-distance 220 --elevation-min-change 0.8
python -m fit_to_md .\tests\fit_files\0001.fit --elevation-source hybrid --dem-sample-distance 30
python -m fit_to_md .\tests\fit_files\0001.fit --elevation-source dem --opentopodata-dataset eudem25m --opentopodata-base-url https://api.opentopodata.org
```

## Per-Kilometer Dynamics

Use `--dynamics-step-size` to control how often heart-rate, pace, and grade samples are emitted from the start to the end of every completed kilometer. `--transition-sample-interval` remains available as an alias for compatibility.

- Smaller intervals increase report detail.
- Larger intervals reduce report size and token usage.

The report renderer is section-based, so adding new output blocks should be done by adding a section renderer instead of editing a single monolithic formatter.

The session summary uses FIT-native weather fields by default. Use `--weather-mode auto` to explicitly enable historical weather lookup based on the activity start location and time.

## Elevation Tuning

Session elevation gain/loss is derived from filtered record altitudes rather than the raw FIT summary totals.

- Use `--elevation-smoothing-distance` to widen or narrow the smoothing span applied before ascent/descent is accumulated.
- Use `--elevation-min-change` to ignore small residual altitude changes after smoothing.
- FIT altitude is used by default and does not require an external request.
- Use `--elevation-source dem` to always replace FIT altitude with DEM samples fetched from OpenTopoData.
- Use `--elevation-source hybrid` to keep FIT altitude when it looks stable and fall back to DEM when the altitude trace is clearly noisy.
- Use `--dem-sample-distance` to control how densely the route is resampled before DEM lookup.
- Use `--opentopodata-dataset` and `--opentopodata-base-url` to switch datasets or target a self-hosted OpenTopoData instance without code changes.
- Larger values generally reduce noisy gain/loss totals and can move output closer to Garmin Connect-style corrected elevation.

When you use the public OpenTopoData API at `https://api.opentopodata.org`, the CLI enforces the documented public-service limits during that run:

- Maximum 100 locations per request.
- Maximum 1 request per second.
- Maximum 1000 requests in a single CLI execution.

The CLI stays stateless and does not persist daily usage across runs. It prints the number of OpenTopoData requests made during the current run at the end on stderr.

When DEM lookup is active in `dem` or `hybrid` mode, the CLI also prints per-request OpenTopoData progress on stderr while the elevation batches are being fetched.

## External Services and Privacy

The default CLI configuration does not contact weather or elevation services. External enrichment is opt-in:

- `--weather-mode auto` sends the activity start location and time to Open-Meteo when FIT weather is unavailable.
- `--elevation-source dem` or `--elevation-source hybrid` sends sampled route coordinates to the configured OpenTopoData service.

Use `--weather-mode fit --elevation-source fit` when activity location data must remain local.
