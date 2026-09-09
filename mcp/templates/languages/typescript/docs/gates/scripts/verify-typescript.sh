#!/usr/bin/env bash
set -euo pipefail

# Verification gate for {{project}}.
# Run from the project root: bash gates/scripts/verify-typescript.sh

echo "=== [1/3] Build ==="
npm run build

echo "=== [2/3] Tests ==="
npm test

echo "=== [3/3] Lint ==="
npm run lint

echo ""
echo "OK - all gates passed"
