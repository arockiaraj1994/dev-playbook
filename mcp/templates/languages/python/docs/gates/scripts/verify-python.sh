#!/usr/bin/env bash
set -euo pipefail

# Verification gate for {{project}}.
# Run from the project root: bash gates/scripts/verify-python.sh

echo "=== [1/4] Format ==="
uv run ruff format --check .

echo "=== [2/4] Lint ==="
uv run ruff check .

echo "=== [3/4] Types ==="
uv run mypy src tests

echo "=== [4/4] Tests ==="
uv run pytest

echo ""
echo "OK - all gates passed"
