#!/bin/sh
# Run a chosen subset of the backend tests against a throwaway PostgreSQL,
# exactly as ./scripts/test-postgres.sh runs the whole suite, without the
# coverage gate (it only makes sense over the full suite).
#
#   ./scripts/test-focused.sh api/tests/integration/test_api_v1.py [more paths/args]
#
# The image is rebuilt every time because it COPYs api/, pages/ and js/ at
# build time. The exit code is pytest's own; the trap only tears down the
# per-run compose project and its tmpfs database.
set -eu
cd "$(dirname "$0")/.."
P="ph-focus-$(date +%s)-$$"
cleanup() { docker compose -p "$P" -f compose.test.yaml down --volumes >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
docker compose -p "$P" -f compose.test.yaml run --build --rm tests \
    python -m pytest -q -o addopts='' "$@"
