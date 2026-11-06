#!/usr/bin/env sh
set -eu
export PARTNERTYPE_ENV=development

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PARTNERTYPE_PORT="${PORT:-8000}"
OLLAMA_BIN=$(command -v ollama 2>/dev/null || true)
OLLAMA_STARTED_BY_SCRIPT=0
OLLAMA_PID=""
OLLAMA_LOG="${TMPDIR:-/tmp}/partnertype-ollama.log"

if [ -z "$OLLAMA_BIN" ] && [ -x "/Applications/Ollama.app/Contents/Resources/ollama" ]; then
  OLLAMA_BIN="/Applications/Ollama.app/Contents/Resources/ollama"
fi

if [ -z "$OLLAMA_BIN" ]; then
  echo "Ollama not found. Install it from https://ollama.com/download."
  exit 1
fi

if command -v lsof >/dev/null 2>&1 && \
  lsof -nP -iTCP:"$PARTNERTYPE_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PARTNERTYPE_PORT is already in use. PartnerType will not be started a second time."
  echo "The process using the port:"
  lsof -nP -iTCP:"$PARTNERTYPE_PORT" -sTCP:LISTEN || true
  echo ""
  echo "If an older PartnerType is running, stop the PID shown and run this script again."
  exit 1
fi

cleanup() {
  if [ "$OLLAMA_STARTED_BY_SCRIPT" -eq 1 ] && [ -n "$OLLAMA_PID" ]; then
    kill "$OLLAMA_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! "$OLLAMA_BIN" list >/dev/null 2>&1; then
  echo "Starting the local Ollama service..."
  OLLAMA_HOST=127.0.0.1:11434 "$OLLAMA_BIN" serve >"$OLLAMA_LOG" 2>&1 &
  OLLAMA_PID=$!
  OLLAMA_STARTED_BY_SCRIPT=1

  attempt=0
  while ! "$OLLAMA_BIN" list >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 15 ]; then
      echo "Ollama did not start. Log: $OLLAMA_LOG"
      exit 1
    fi
    sleep 1
  done
fi

if ! "$OLLAMA_BIN" show partnerhub-agent >/dev/null 2>&1; then
  echo "The partnerhub-agent model is missing. Running the one-time installation."
  "$PROJECT_ROOT/scripts/setup-local-agent.sh"
fi

echo "Checking the partnerhub-agent model..."
"$OLLAMA_BIN" show partnerhub-agent >/dev/null

echo "Starting PartnerType: http://localhost:$PARTNERTYPE_PORT"
cd "$PROJECT_ROOT"
PORT="$PARTNERTYPE_PORT" python3 server.py
