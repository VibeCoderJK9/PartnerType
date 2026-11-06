#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
TEST_PROJECT="ph-tests-$(date +%s)-$$"
cleanup() { docker compose -p "$TEST_PROJECT" -f compose.test.yaml down --volumes; }
trap cleanup EXIT INT TERM
docker compose -p "$TEST_PROJECT" -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
