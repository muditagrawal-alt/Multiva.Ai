#!/usr/bin/env bash
#
# Start Multiva: check what it needs, start what is missing, open the studio.
#
#   ./run.sh                  desktop app, local model through Ollama
#   ./run.sh --web            in a browser instead of the desktop window
#   ./run.sh --provider groq  a hosted script model
#   ./run.sh --port 8123      somewhere other than 8000
#   ./run.sh --fresh          as a brand new user, without touching your setup
#
# Ctrl-C stops the engine. Nothing is installed without saying so first.

set -u

cd "$(dirname "$0")"

PORT=8000
FRESH=0
PROVIDER=ollama
MODEL=""
MODE=desktop
while [ $# -gt 0 ]; do
    case "$1" in
        --provider) PROVIDER="${2:-}"; shift 2 ;;
        --model)    MODEL="${2:-}";    shift 2 ;;
        --port)     PORT="${2:-}";     shift 2 ;;
        --fresh)    FRESH=1;           shift ;;
        --web)      MODE=web;          shift ;;
        --desktop)  MODE=desktop;      shift ;;
        -h|--help)  sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1"; exit 2 ;;
    esac
done

say()  { printf '  %s\n' "$*"; }
fail() { printf '\n  %s\n\n' "$*" >&2; exit 1; }

printf '\n  Multiva\n  %s\n' "------------------------------------------------"

if [ "$FRESH" = "1" ]; then
    SANDBOX="${TMPDIR:-/tmp}/multiva-fresh-$$"
    mkdir -p "$SANDBOX/projects"
    export MULTIVA_ENGINES="$SANDBOX/engines.json"
    export MULTIVA_SETTINGS="$SANDBOX/llm.json"
    export MULTIVA_OUTPUT_DIR="$SANDBOX/projects"
    say "First-run mode. Your real settings are untouched."
    say "Sandbox: $SANDBOX"
    say "Models are shared, so nothing is downloaded twice."
fi

# --- what it cannot start without -----------------------------------------
PY=./venv/bin/python
[ -x "$PY" ] || PY=./.venv/bin/python
[ -x "$PY" ] || fail "No Python environment. Run:
    python3.10 -m venv venv && ./venv/bin/pip install -r requirements.txt"

command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is not installed. Run:
    brew install ffmpeg        (macOS)
    sudo apt install ffmpeg    (Ubuntu)"

# --- the interface is a build artefact, not in the repository ---------------
if [ ! -d web ]; then
    say "Building the studio interface (first run only)..."
    command -v npm >/dev/null 2>&1 || fail "npm is not installed, and web/ has not been built."
    ( cd frontend && npm install --silent && npm run build >/dev/null ) \
        || fail "The interface failed to build. Run it by hand:
    cd frontend && npm install && npm run build"
    say "Interface built."
fi

# --- the script model, which is optional but usually wanted -----------------
if [ "$PROVIDER" = "ollama" ]; then
    WANT="${MODEL:-qwen2.5:7b}"
    if ! curl -sf --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        if command -v ollama >/dev/null 2>&1; then
            say "Starting Ollama..."
            ( ollama serve >/dev/null 2>&1 & )
            for _ in $(seq 1 20); do
                curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
                sleep 1
            done
        fi
    fi
    if curl -sf --max-time 3 http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q "\"${WANT}\""; then
        say "Script model: ${WANT} on this machine."
    elif curl -sf --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        say "Ollama is running but ${WANT} is not pulled."
        say "Pull it with:  ollama pull ${WANT}"
        say "Dubbing works without it; only fitting a long line needs it."
    else
        say "Ollama is not running. Dubbing works without it;"
        say "only fitting a long line needs it. Start it with:  ollama serve"
    fi
else
    say "Script model: ${PROVIDER}${MODEL:+ / $MODEL} (hosted)."
fi

# --- the desktop window, unless a browser was asked for --------------------
APP_BUNDLE="frontend/src-tauri/target/release/bundle/macos/Multiva Studio.app"
APP_LINUX="frontend/src-tauri/target/release/multiva-studio"
DESKTOP=""
if [ "$MODE" = "desktop" ]; then
    if [ -d "$APP_BUNDLE" ]; then
        DESKTOP="$APP_BUNDLE"
    elif [ -x "$APP_LINUX" ]; then
        DESKTOP="$APP_LINUX"
    else
        say "No desktop build found, opening in a browser instead."
        say "Build the desktop app with:  cd frontend/src-tauri && cargo build --release"
        MODE=web
    fi
fi

# --- do not fight something already on the port ----------------------------
if lsof -ti:"$PORT" >/dev/null 2>&1; then
    say "Something is already serving port ${PORT}; opening that instead."
    if [ -n "$DESKTOP" ]; then
        open "$DESKTOP" 2>/dev/null || "$DESKTOP" >/dev/null 2>&1 &
    else
        open "http://127.0.0.1:${PORT}/app/" 2>/dev/null \
            || xdg-open "http://127.0.0.1:${PORT}/app/" 2>/dev/null \
            || say "Open http://127.0.0.1:${PORT}/app/"
    fi
    exit 0
fi

# --- open the studio once the engine says it is ready ----------------------
(
    for _ in $(seq 1 300); do
        if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/api/boot" 2>/dev/null \
             | grep -q '"ready": *true'; then
            if [ -n "$DESKTOP" ]; then
                printf '\n  Engine ready. Opening the studio window.\n\n'
                open "$DESKTOP" 2>/dev/null || "$DESKTOP" >/dev/null 2>&1 &
            else
                printf '\n  Studio ready at http://127.0.0.1:%s/app/\n\n' "$PORT"
                open "http://127.0.0.1:${PORT}/app/" 2>/dev/null \
                    || xdg-open "http://127.0.0.1:${PORT}/app/" 2>/dev/null
            fi
            exit 0
        fi
        sleep 1
    done
) &

say "Starting the engine on port ${PORT}. Models load on first use."
say "Ctrl-C stops it."
printf '  %s\n\n' "------------------------------------------------"

cd Backend_pipeline
export MULTIVA_LLM_PROVIDER="$PROVIDER"
[ -n "$MODEL" ] && export MULTIVA_LLM_MODEL="$MODEL"
export PYTORCH_ENABLE_MPS_FALLBACK=1
export TOKENIZERS_PARALLELISM=false
exec "../${PY#./}" -m uvicorn app:app --port "$PORT"
