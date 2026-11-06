#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

OLLAMA_BIN=$(command -v ollama 2>/dev/null || true)
if [ -z "$OLLAMA_BIN" ] && [ -x "/Applications/Ollama.app/Contents/Resources/ollama" ]; then
  OLLAMA_BIN="/Applications/Ollama.app/Contents/Resources/ollama"
fi

if [ -z "$OLLAMA_BIN" ]; then
  echo "Ollama not found. Install it from https://ollama.com/download and start the app."
  exit 1
fi

if ! "$OLLAMA_BIN" list >/dev/null 2>&1; then
  echo "Ollama is installed, but the service is not running. Start the Ollama app or run: ollama serve"
  exit 1
fi

# The base model is whatever the Modelfile declares; naming it here as well is
# how a changed FROM ends up downloading a model nobody uses.
BASE=$(sed -n 's/^[[:space:]]*FROM[[:space:]]\{1,\}\([^[:space:]]\{1,\}\)[[:space:]]*$/\1/p' "$PROJECT_ROOT/Modelfile" | head -n 1)
if [ -z "$BASE" ]; then
  echo "Modelfile has no FROM line: $PROJECT_ROOT/Modelfile"
  exit 1
fi

echo "Pulling the base model $BASE..."
"$OLLAMA_BIN" pull "$BASE"

echo "Building the local partnerhub-agent model..."
"$OLLAMA_BIN" create partnerhub-agent -f "$PROJECT_ROOT/Modelfile"

echo "Done. Installed model:"
"$OLLAMA_BIN" show partnerhub-agent
echo "Start PartnerType with: python3 server.py"
