#!/bin/sh

set -eu

project_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_directory"

if [ -n "${FIT_TO_MD_PYTHON:-}" ]; then
    python_executable=$FIT_TO_MD_PYTHON
elif [ -x "$project_directory/.venv/bin/python" ]; then
    python_executable="$project_directory/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    python_executable=python3
else
    echo "Python 3 was not found. Create .venv or set FIT_TO_MD_PYTHON." >&2
    exit 127
fi

echo "Running Ruff lint checks..."
"$python_executable" -m ruff check src tests

echo "Checking Ruff formatting..."
"$python_executable" -m ruff format --check src tests

echo "Running mypy..."
"$python_executable" -m mypy

echo "Running pytest with coverage..."
"$python_executable" -m pytest
