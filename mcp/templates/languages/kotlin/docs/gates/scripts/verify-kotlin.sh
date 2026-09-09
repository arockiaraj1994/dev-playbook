#!/usr/bin/env bash
set -euo pipefail

# Verification gate for {{project}}.
# Run from the project root: bash gates/scripts/verify-kotlin.sh

echo "=== [1/3] Build ==="
./gradlew build

echo "=== [2/3] Tests ==="
./gradlew test

echo "=== [3/3] Lint ==="
./gradlew ktlintCheck

echo ""
echo "OK - all gates passed"
