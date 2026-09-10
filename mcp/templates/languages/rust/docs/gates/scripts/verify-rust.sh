#!/usr/bin/env bash
set -euo pipefail

# Verification gate for {{project}}.
# Run from the project root: bash gates/scripts/verify-rust.sh

echo "=== [1/5] Format ==="
cargo fmt --all -- --check

echo "=== [2/5] Lint ==="
cargo clippy --all-targets --all-features -- -D warnings

echo "=== [3/5] Tests ==="
cargo test --all-features

echo "=== [4/5] Docs ==="
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps --all-features

echo "=== [5/5] Advisories ==="
cargo deny check advisories bans licenses

echo ""
echo "OK - all gates passed"
