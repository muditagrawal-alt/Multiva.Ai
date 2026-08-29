#!/bin/bash
# =============================================================================
# Multiva Studio launcher
#
# Double-click this file in Finder. It starts the local API, waits for it to
# answer, and opens the studio in the default browser. Closing the Terminal
# window it opens stops the server.
#
# This exists so nobody has to know what a venv or uvicorn is to use the app.
# =============================================================================

set -u
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
PORT="${MULTIVA_PORT:-8000}"

printf '\n  Multiva Studio\n  --------------\n\n'

# --- dependencies ------------------------------------------------------------
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  ffmpeg is required and was not found."
  echo "  Install it with:  brew install ffmpeg"
  echo
  read -r -p "  Press return to close."
  exit 1
fi

VENV_PY="$ROOT/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "  First run: creating the Python environment."
  echo "  This downloads a few GB and takes a while. It only happens once."
  echo
  PY=$(command -v python3.10 || command -v python3)
  if [ -z "$PY" ]; then
    echo "  Python 3.10 or newer is required and was not found."
    read -r -p "  Press return to close."
    exit 1
  fi
  "$PY" -m venv "$ROOT/venv" || { echo "  Could not create the environment."; read -r -p "  Press return to close."; exit 1; }
  "$VENV_PY" -m pip install --upgrade pip >/dev/null
  "$VENV_PY" -m pip install -r "$ROOT/requirements.txt" || {
    echo "  Dependency install failed. Scroll up for the reason."
    read -r -p "  Press return to close."; exit 1; }
  echo
fi

# --- already running? --------------------------------------------------------
if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "  Studio is already running. Opening it."
  open "http://127.0.0.1:$PORT/app/"
  exit 0
fi

# --- start -------------------------------------------------------------------
echo "  Starting on port $PORT."
echo "  Models load in the background; the first clone is slower than the rest."
echo "  Keep this window open. Close it to stop the studio."
echo

cd "$ROOT/Backend_pipeline" || exit 1
PYTORCH_ENABLE_MPS_FALLBACK=1 TOKENIZERS_PARALLELISM=false \
  "$VENV_PY" -m uvicorn app:app --port "$PORT" &
SERVER_PID=$!

# Stop the server when this window closes rather than orphaning it.
trap 'kill $SERVER_PID 2>/dev/null' EXIT INT TERM

for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "  Ready. Opening the studio."
    open "http://127.0.0.1:$PORT/app/"
    break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "  The server stopped during startup."; break; }
  sleep 1
done

wait $SERVER_PID
