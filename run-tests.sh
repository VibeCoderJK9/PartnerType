#!/usr/bin/env bash
#
# run-tests.sh -- single-command full-suite runner.
#
# Runs the backend suite (pytest, with coverage) and the frontend suite
# (vitest, with coverage) unconditionally -- both stacks always run, even if
# the first one fails -- so a single invocation reports the complete picture.
# Exits non-zero if either stack fails.

set -uo pipefail

backend_status=0
frontend_status=0

backend_log="$(mktemp -t partnertype-pytest)"
trap 'rm -f "$backend_log"' EXIT

echo "==> Running backend suite (pytest)"
./scripts/test-postgres.sh 2>&1 | tee "$backend_log"
backend_status=${PIPESTATUS[0]}

echo "==> Running frontend suite (vitest, with coverage)"
npx vitest run --coverage
frontend_status=$?

echo ""
echo "Backend  (pytest):  $([ "$backend_status" -eq 0 ] && echo PASSED || echo FAILED)"
echo "Frontend (vitest):  $([ "$frontend_status" -eq 0 ] && echo PASSED || echo FAILED)"

# The frontend coverage report is long enough to bury a backend failure far
# above the summary, so name the failing tests again down here where the
# PASSED/FAILED lines are read.
if [ "$backend_status" -ne 0 ]; then
  echo ""
  echo "Failing backend tests:"
  grep -E '^(FAILED|ERROR) ' "$backend_log" || echo "  (see the pytest output above)"
fi

if [ "$backend_status" -ne 0 ] || [ "$frontend_status" -ne 0 ]; then
  exit 1
fi

exit 0
