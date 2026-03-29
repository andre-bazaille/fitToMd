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
```

The FIT parsing adapter is scaffolded but not implemented yet. The Markdown rendering path and application boundaries are in place so feature work can proceed without restructuring the project.
