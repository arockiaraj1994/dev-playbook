#!/usr/bin/env bash
set -euo pipefail

# Verification gate for {{project}}.
# Run from the project root: bash gates/scripts/verify-go.sh

echo "=== [1/5] Format ==="
test -z "$(gofmt -l .)" || { echo "unformatted files:"; gofmt -l .; exit 1; }

echo "=== [2/5] Vet ==="
go vet ./...

echo "=== [3/5] Lint ==="
go run honnef.co/go/tools/cmd/staticcheck@latest ./...

echo "=== [4/5] Tests ==="
go test -race -count=1 ./...

echo "=== [5/5] Vulnerabilities ==="
go run golang.org/x/vuln/cmd/govulncheck@latest ./...

echo ""
echo "OK - all gates passed"
