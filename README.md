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
```

## Transition Sampling

Use `--transition-sample-interval` to control how often recovery/ramp samples are emitted and `--transition-window` to control how many seconds before and after each lap boundary are included.

- Smaller intervals increase report detail.
- Larger windows capture longer recoveries or ramps.

The report renderer is section-based, so adding new output blocks should be done by adding a section renderer instead of editing a single monolithic formatter.

The session summary uses FIT-native weather fields when available and, by default, falls back to historical weather lookup based on the activity start location and time. Use `--weather-mode fit` to disable the external fallback.
