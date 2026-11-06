#!/bin/sh
# Vulnerability scan of what this application actually ships: its Python
# dependencies, its JavaScript dependencies, its own DOM sinks, and the
# container image built from them.
#
# Everything runs in throwaway containers, so the scanners themselves never
# become dependencies of the project. Run before every release and whenever a
# pinned version changes -- a pinned digest is safe until the day it is not,
# and nothing else here would tell you which day that was.
set -eu
cd "$(dirname "$0")/.."
FAILED=''
# Scanners are pinned like everything else they run next to: a floating tag
# hands whatever is published under it on the day of the scan to this
# machine. Update the version and the digest together, deliberately.
PIP_AUDIT_VERSION='2.10.1'
TRIVY_IMAGE='aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c'

echo '=== Python dependencies (pip-audit) ==='
docker run --rm -v "$PWD/requirements.txt:/req.txt:ro" python:3.12-slim sh -c \
  "pip install --quiet 'pip-audit==$PIP_AUDIT_VERSION' >/dev/null 2>&1 && pip-audit -r /req.txt --progress-spinner off" \
  || FAILED="$FAILED python"

echo
echo '=== JavaScript dependencies (npm audit) ==='
npm audit --omit=dev || FAILED="$FAILED javascript"

echo
echo '=== DOM sinks (own audit) ==='
node scripts/security-sink-audit.mjs || FAILED="$FAILED sinks"

echo
echo '=== Container image (trivy) ==='
if [ "${1:-}" = '--with-image' ]; then
    # Scans the release image, so base-image findings show up too. Skipped by
    # default because it pulls the scanner and a fresh vulnerability database.
    # The scanner reads a saved copy of the image: it never gets the Docker
    # socket, which would be root on this machine.
    docker build --target release -t partnertype:scan . >/dev/null
    SCAN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/partnertype-scan.XXXXXX")
    trap 'rm -rf "$SCAN_DIR"' EXIT
    docker save partnertype:scan -o "$SCAN_DIR/image.tar"
    chmod 0644 "$SCAN_DIR/image.tar"; chmod 0755 "$SCAN_DIR"
    docker run --rm -v "$SCAN_DIR:/scan:ro" "$TRIVY_IMAGE" \
        image --input /scan/image.tar --severity HIGH,CRITICAL --ignore-unfixed \
        || FAILED="$FAILED image"
else
    echo 'skipped (pass --with-image to include it)'
fi

echo
if [ -n "$FAILED" ]; then
    echo "FAILED:$FAILED"
    exit 1
fi
echo 'All scans clean.'
