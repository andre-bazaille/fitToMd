# fitToMd

`fitToMd` is a Python CLI project for converting Garmin FIT files into structured Markdown reports suitable for LLM-based coaching analysis.

## Project Layout

- `src/fit_to_md/domain/`: core domain entities and ports.
- `src/fit_to_md/application/`: use-case orchestration.
- `src/fit_to_md/infrastructure/`: FIT decoding and Markdown rendering adapters.
- `tests/`: unit tests for application and infrastructure behavior.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
python -m fit_to_md --help
python -m fit_to_md .\tests\fit_files\2026-03-24-12-20-27.fit --transition-sample-interval 5 --transition-window 90
python -m fit_to_md .\tests\fit_files\2026-03-24-12-20-27.fit --elevation-smoothing-distance 220 --elevation-min-change 0.8
python -m fit_to_md .\tests\fit_files\2026-03-24-12-20-27.fit --elevation-source hybrid --dem-sample-distance 30
python -m fit_to_md .\tests\fit_files\2026-03-24-12-20-27.fit --elevation-source dem --opentopodata-dataset eudem25m --opentopodata-base-url https://api.opentopodata.org
```

## Transition Sampling

Use `--transition-sample-interval` to control how often recovery/ramp samples are emitted and `--transition-window` to control how many seconds before and after each lap boundary are included.

- Smaller intervals increase report detail.
- Larger windows capture longer recoveries or ramps.

The report renderer is section-based, so adding new output blocks should be done by adding a section renderer instead of editing a single monolithic formatter.

The session summary uses FIT-native weather fields when available and, by default, falls back to historical weather lookup based on the activity start location and time. Use `--weather-mode fit` to disable the external fallback.

## Elevation Tuning

Session elevation gain/loss is derived from filtered record altitudes rather than the raw FIT summary totals.

- Use `--elevation-smoothing-distance` to widen or narrow the smoothing span applied before ascent/descent is accumulated.
- Use `--elevation-min-change` to ignore small residual altitude changes after smoothing.
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
