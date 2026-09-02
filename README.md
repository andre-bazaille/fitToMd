# fitToMd

[![CI](https://github.com/andre-bazaille/fitToMd/actions/workflows/ci.yml/badge.svg)](https://github.com/andre-bazaille/fitToMd/actions/workflows/ci.yml)

`fitToMd` converts Garmin/FIT activity files into compact, structured Markdown.
The result is designed for LLM-based coaching and activity review without having
to upload a binary FIT file.

The generated report includes:

- a session summary with duration, distance, pace or speed, heart rate, cadence,
  elevation, and available weather;
- completed one-kilometer splits;
- configurable per-kilometer heart-rate, pace or speed, and grade samples;
- pause-aware timing derived from FIT timer events;
- optional historical weather from Open-Meteo; and
- optional terrain elevation from OpenTopoData.

External enrichment is disabled by default. A normal conversion stays local and
does not send activity coordinates to a third party.

## Requirements

- Python 3.12 or newer
- A FIT activity containing session data and, for the richest report, timestamped
  record data

## Installation

The project is currently installed from source:

```bash
git clone https://github.com/andre-bazaille/fitToMd.git
cd fitToMd
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

On Windows PowerShell, activate the environment with:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

## Quick start

```bash
fit-to-md activity.fit
```

This writes `activity.md` next to the FIT file and prints the same Markdown to
standard output. Use `--output` to select a different destination:

```bash
fit-to-md activity.fit --output reports/activity.md
```

`python -m fit_to_md` can be used instead of the `fit-to-md` command. Run
`fit-to-md --help` for the complete CLI reference.

## Example output

### FIT Report: 2020-01-01 Running

#### Session Summary

- **Start Time:** 2020-01-01 12:00:00+00:00
- **Activity Type:** Running
- **Total Distance:** 11.99 km
- **Total Time:** 1:01:19
- **Elapsed Time:** 1:01:19
- **Elevation Gain/Loss:** +67m / -69m
- **Avg/Max HR:** 140 / 150 bpm
- **Avg Cadence:** 160 spm
- **Avg Pace:** 5:06/km
- **Weather:** 3.2C, feels like 0.2C, Overcast, Wind 8.8 km/h SE [historical]

#### Kilometric Splits

| Km | Time | Pace | Elev +/- | Avg HR | Max HR | Avg Cad |
|---|---|---|---|---|---|---|
| 1 | 4:45 | 4:45 | -6m | 113 | 133 | 160 |
| 2 | 5:07 | 5:07 | -2m | 138 | 145 | 162 |
| 3 | 5:06 | 5:06 | +2m | 140 | 142 | 162 |
| 4 | 5:22 | 5:22 | +6m | 142 | 147 | 160 |
| 5 | 5:01 | 5:01 | -3m | 141 | 145 | 162 |
| 6 | 4:59 | 4:59 | -4m | 142 | 145 | 161 |
| 7 | 5:00 | 5:00 | +2m | 142 | 145 | 161 |
| 8 | 5:20 | 5:20 | +1m | 145 | 148 | 160 |
| 9 | 5:13 | 5:13 | -4m | 145 | 149 | 159 |
| 10 | 4:59 | 4:59 | +0m | 145 | 147 | 162 |
| 11 | 5:01 | 5:01 | -1m | 145 | 148 | 162 |

#### Heart Rate Dynamics (Per Kilometer)

- **Km 1**
  - 0:00: 78 bpm (Pace: -)
  - 1:00: 103 bpm (Pace: 5:22/km, Grade: 2.40%)
  - 2:00: 115 bpm (Pace: 5:24/km, Grade: -2.38%)
  - 3:00: 123 bpm (Pace: 4:52/km, Grade: -3.05%)
  - 4:00: 122 bpm (Pace: 4:32/km, Grade: -1.64%)
  - 4:46: 134 bpm (Pace: 4:34/km, Grade: 1.19%)
- **Km 2**
  - 0:00: 134 bpm (Pace: 4:34/km, Grade: 1.19%)
  - 1:00: 136 bpm (Pace: 4:51/km, Grade: 0.36%)
  - 2:00: 138 bpm (Pace: 5:11/km, Grade: 0.78%)
  - 3:00: 139 bpm (Pace: 5:16/km, Grade: -1.57%)
  - 4:00: 140 bpm (Pace: 5:07/km, Grade: 1.05%)
  - 5:00: 141 bpm (Pace: 4:56/km, Grade: -0.85%)
  - 5:07: 140 bpm (Pace: 4:55/km, Grade: -0.54%)

The full report continues for every completed kilometer.

## Elevation sources and route smoothing

Elevation processing has two separate stages:

1. **Choose the altitude source.** Keep altitude recorded in the FIT file, or
   replace it with terrain elevations queried along the GPS route.
2. **Filter the selected altitude trace.** Smooth short fluctuations and apply a
   minimum-change threshold before accumulating session ascent and descent.

"Route smoothing" here means smoothing the altitude profile along traveled
distance. It does **not** alter, simplify, or snap the recorded GPS itinerary.

### Choosing an elevation source

| Mode | Network use | Behavior |
|---|---:|---|
| `fit` (default) | None | Uses the altitude stored in the FIT records. |
| `dem` | OpenTopoData | Replaces available record altitudes with terrain elevations. |
| `hybrid` | OpenTopoData | Queries terrain elevation, but replaces FIT altitude only when the FIT trace is clearly noisy. |

Use terrain elevation for every eligible route:

```bash
fit-to-md activity.fit --elevation-source dem
```

Or let the noise check decide whether to replace FIT altitude:

```bash
fit-to-md activity.fit --elevation-source hybrid
```

Hybrid mode is not an offline automatic mode: it still sends route samples to
OpenTopoData so that it can compare the two profiles. The comparison requires at
least five aligned FIT/DEM points and considers excessive total variation,
disagreement with the terrain model, and frequent changes in climb direction.
Stable FIT altitude is retained.

DEM lookup requires at least two records containing distance and GPS coordinates.
If there is insufficient route data, the selected dataset has no coverage, or
OpenTopoData returns too few usable elevations, the original FIT altitude is
retained where it cannot be replaced.

### OpenTopoData datasets and servers

The default configuration uses the public OpenTopoData API and its `eudem25m`
dataset:

```bash
fit-to-md activity.fit \
  --elevation-source dem \
  --opentopodata-dataset eudem25m
```

`eudem25m` is a 25 m DEM for Europe; it is not a worldwide dataset. Select a
dataset that covers the activity location, for example `srtm30m` for latitudes
between 60° south and 60° north:

```bash
fit-to-md activity.fit \
  --elevation-source dem \
  --opentopodata-dataset srtm30m
```

See the official [public dataset list](https://www.opentopodata.org/#public-api)
for coverage and resolution, and the [API documentation](https://www.opentopodata.org/api/)
for service behavior.

To use a self-hosted OpenTopoData instance, supply both its base URL and the
dataset name configured on that server:

```bash
fit-to-md activity.fit \
  --elevation-source dem \
  --opentopodata-base-url http://localhost:5000 \
  --opentopodata-dataset my-dem
```

The OpenTopoData project provides a [self-hosting guide](https://www.opentopodata.org/server/).
Limits and dataset availability for a self-hosted instance are controlled by
that server, not by `fitToMd`.

### DEM route sampling

Before lookup, `fitToMd` resamples the recorded itinerary by traveled distance.
The first and last route points are included and intermediate coordinates are
linearly interpolated every 25 m by default. OpenTopoData uses bilinear raster
interpolation for those samples, and returned elevations are interpolated back
onto the FIT records by distance.

Change the spacing with `--dem-sample-distance`:

```bash
fit-to-md activity.fit \
  --elevation-source dem \
  --dem-sample-distance 50
```

- A smaller distance follows local terrain more closely but sends more
  coordinates and takes longer.
- A larger distance reduces requests and runtime but can miss short terrain
  features.
- Dataset resolution is the practical accuracy limit; sampling much more densely
  than the DEM grid does not create more detailed source data.

For the public API, the CLI batches at most 100 locations per request, starts at
most one request per second, and refuses a conversion that would exceed 1,000
requests in that run. OpenTopoData documents a separate 1,000-calls-per-day
public-service limit. `fitToMd` reports progress and its per-run request count on
standard error, but it does not track usage across separate CLI runs.

### Elevation gain/loss smoothing

After the source is selected, session gain and loss are calculated from record
altitudes instead of trusting FIT session totals:

1. A centered moving average smooths the altitude samples. Its odd-sized window
   approximates `--elevation-smoothing-distance` along the route (default:
   `170` m).
2. Starting at the first smoothed altitude, a rise or fall is accumulated only
   when it reaches `--elevation-min-change` from the last accepted altitude
   (default: `0.4` m).
3. If fewer than two record altitudes are available, the report falls back to
   session totals and then lap totals.

The two smoothing options affect the session gain/loss totals. Split elevation
is the net altitude difference across each kilometer. Dynamics use the native
FIT grade when available; after DEM replacement, grade is estimated over a
smoothed 200 m altitude span.

Example with stronger filtering:

```bash
fit-to-md activity.fit \
  --elevation-smoothing-distance 250 \
  --elevation-min-change 0.8
```

Practical tuning guidance:

- If gain/loss is implausibly high on a steady route, increase the smoothing
  distance or minimum change.
- If short, genuine climbs disappear, decrease one or both values.
- Change one setting at a time and compare several known activities. Device
  sampling, barometer behavior, DEM resolution, and other platforms' proprietary
  corrections mean there is no universal setting that reproduces every service.

## Per-kilometer dynamics

Use `--dynamics-step-size` to control the sampling interval for heart rate, pace
or speed, and grade within every completed kilometer. The default is 30 seconds.

```bash
fit-to-md activity.fit --dynamics-step-size 10
```

Smaller intervals increase detail and report size; larger intervals reduce both.
The kilometer start and finish are included even when they do not fall exactly
on the requested interval. `--transition-sample-interval` remains available as
a compatibility alias.

## Weather enrichment

FIT-native temperature/weather is used when present. Historical lookup is
explicitly enabled with:

```bash
fit-to-md activity.fit --weather-mode auto
```

When FIT weather is unavailable, this sends the activity start coordinates and
time to Open-Meteo. If lookup fails or the FIT file has no start location/time,
the report remains usable and states that weather is unavailable.

## Configuration file

Use `--config PATH` for reusable defaults. Each line has the form
`option = value`, without the leading `--`. Blank lines and comments beginning
with `#` are ignored; underscores in option names are also accepted.

```ini
# fit-to-md.conf
dynamics-step-size = 10
weather-mode = fit
elevation-source = hybrid
dem-sample-distance = 30
elevation-smoothing-distance = 200
elevation-min-change = 0.6
```

```bash
fit-to-md activity.fit --config ./fit-to-md.conf
```

An option supplied directly on the command line overrides the same option in the
file. Configurable options are `output`, `dynamics-step-size`, `weather-mode`,
`elevation-smoothing-distance`, `elevation-min-change`, `elevation-source`,
`dem-sample-distance`, `opentopodata-dataset`, and `opentopodata-base-url`.

## Privacy and public FIT fixtures

Use the fully local settings when activity coordinates must not leave the
machine:

```bash
fit-to-md activity.fit --weather-mode fit --elevation-source fit
```

Real FIT files may contain personal profile, device, health, workout, course,
timestamp, and location data. Before committing a FIT file as a public test
fixture, create an explicit sanitized copy:

```bash
fit-sanitize private-activity.fit --output activity.public.fit
```

The sanitizer keeps only file identity, activity, session, sport, lap, record,
and event messages. It removes serial numbers, developer and unknown fields, and
other messages. The activity timeline is shifted to `2020-01-01T12:00:00Z` by
default while durations are preserved. Use `--start-at` to choose another
synthetic ISO 8601 timestamp.

**Sanitization deliberately preserves the complete GPS track.** A route can
identify a home, workplace, or regular training location. Inspect the output and
only publish it when retaining that track is intentional. The mandatory output
path also prevents accidentally reusing a private timestamp embedded in the
source filename.

## Exit codes

- `0`: report generated and written successfully
- `1`: invalid FIT data, processing failure, read failure, or write failure
- `2`: invalid CLI arguments or an input path that is missing or not a file

## Architecture

The code uses Domain-Driven Design with decoder-independent activity and
reporting domains. FIT decoding, Markdown rendering, configuration, weather, and
elevation are infrastructure adapters composed by the CLI.

- `src/fit_to_md/domain/activity/`: normalized activity entities
- `src/fit_to_md/domain/reporting/`: report entities, ports, and calculations
- `src/fit_to_md/application/`: use-case orchestration
- `src/fit_to_md/infrastructure/`: FIT, Markdown, weather, elevation, and config adapters
- `tests/`: unit, integration, and architecture tests

See [docs/architecture.md](docs/architecture.md) for bounded contexts and
dependency rules.

## Development

Install the development dependencies and run the same quality gate as CI:

```bash
python -m pip install -e '.[dev]'
./ci.sh
```

On Windows, run the equivalent commands directly:

```powershell
ruff check src tests
ruff format --check src tests
mypy
pytest
```

The checks include Ruff linting and formatting, strict mypy validation, and
pytest with statement and branch coverage. Coverage must remain at or above 80%.
Behavior changes require unit tests, and external services must be tested with
fakes rather than live network calls.
