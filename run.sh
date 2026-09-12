#!/usr/bin/env bash
#
# Start Multiva: check what it needs, start what is missing, open the studio.
#
#   ./run.sh                  desktop app, local model through Ollama
#   ./run.sh --provider groq  a hosted script model
#   ./run.sh --port 8123      somewhere other than 8000
#   ./run.sh --fresh          as a brand new user, without touching your setup
#   ./run.sh --sandbox DIR    the same, but in a folder you keep and can inspect
#   ./run.sh --yes            set everything up without asking (unattended)
#
# On a fresh clone this offers to build the Python environment and fetch the
# models itself, so nobody has to open an editor or type pip commands. It only
# ever asks once, and says how much it is about to download.
#
# Ctrl-C stops the engine. Nothing is installed without saying so first.

set -u

cd "$(dirname "$0")"

PORT=8000
FRESH=0
ASSUME_YES=0
SANDBOX_DIR=""
PROVIDER=ollama
MODEL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --provider) PROVIDER="${2:-}"; shift 2 ;;
        --model)    MODEL="${2:-}";    shift 2 ;;
        --port)     PORT="${2:-}";     shift 2 ;;
        --fresh)    FRESH=1;           shift ;;
        --yes|-y)   ASSUME_YES=1;      shift ;;
        --sandbox)  FRESH=1; SANDBOX_DIR="${2:-}"; shift 2 ;;
        -h|--help)  sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1"; exit 2 ;;
    esac
done

say()  { printf '  %s\n' "$*"; }
fail() { printf '\n  %s\n\n' "$*" >&2; exit 1; }

printf '\n  Multiva\n  %s\n' "------------------------------------------------"

if [ "$FRESH" = "1" ]; then
    # A named sandbox persists between runs, so a test can be picked up where
    # it was left and the files inspected afterwards. An unnamed one is
    # thrown away with the temp directory.
    SANDBOX="${SANDBOX_DIR:-${TMPDIR:-/tmp}/multiva-fresh-$$}"
    mkdir -p "$SANDBOX/projects"
    export MULTIVA_ENGINES="$SANDBOX/engines.json"
    export MULTIVA_SETTINGS="$SANDBOX/llm.json"
    export MULTIVA_OUTPUT_DIR="$SANDBOX/projects"
    say "First-run mode. Your real settings are untouched."
    say "Sandbox: $SANDBOX"
    say "Models are shared, so nothing is downloaded twice."
fi

# --- what it cannot install for you ---------------------------------------
# ffmpeg needs a package manager and an administrator. Everything else below
# this line, it will offer to do.
command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is not installed. Run:
    brew install ffmpeg        (macOS)
    sudo apt install ffmpeg    (Ubuntu)
then start Multiva again."

# Ask, unless nobody is there to answer.
confirm() {
    [ "$ASSUME_YES" = "1" ] && return 0
    [ -t 0 ] || return 1
    printf '  %s [Y/n] ' "$1"
    read -r reply
    case "$reply" in [Nn]*) return 1 ;; *) return 0 ;; esac
}

find_python() {
    for c in python3.10 python3.11 python3.12 python3; do
        command -v "$c" >/dev/null 2>&1 || continue
        "$c" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' \
            2>/dev/null && { echo "$c"; return 0; }
    done
    return 1
}

PY=./venv/bin/python
[ -x "$PY" ] || PY=./.venv/bin/python
if [ ! -x "$PY" ]; then
    BOOT_PY=$(find_python) || fail "Python 3.10 or newer is not installed. Run:
    brew install python@3.10        (macOS)
    sudo apt install python3.10 python3.10-venv    (Ubuntu)
then start Multiva again."

    say ""
    say "Multiva needs a Python environment before it can run."
    say "It is about 2 GB, goes in venv/ inside this folder, and touches"
    say "nothing else on your machine. Deleting that folder undoes it."
    say ""
    if ! confirm "Set it up now?"; then
        fail "Nothing was changed. When you are ready:
    $BOOT_PY -m venv venv && ./venv/bin/pip install -r requirements.txt"
    fi
    say "Building the environment. This takes a few minutes..."
    "$BOOT_PY" -m venv venv || fail "Could not create the environment."
    PY=./venv/bin/python
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install -r requirements.txt || fail "Some packages failed to
install. The output above says which. Nothing else was changed."
    say "Environment ready."
fi

# --- the models -----------------------------------------------------------
# Roughly 7.5 GB, verified by checksum, resumable. Without them the first
# render fails several minutes in, which is a bad way to find out.
if ! "$PY" scripts/download_models.py --check 2>/dev/null | grep -q "0 item"; then
    missing=$("$PY" scripts/download_models.py --check 2>/dev/null \
              | grep -oE "^ +[0-9]+ item" | tr -dc "0-9")
    say ""
    say "${missing:-Some} model file(s) still need downloading, about 7.5 GB in"
    say "total. It is resumable and checked against a SHA-256, and it only"
    say "happens once."
    say ""
    if confirm "Download them now?"; then
        "$PY" scripts/download_models.py || fail "The download did not finish.
Run Multiva again to pick up where it stopped."
    else
        say "Skipping. Dubbing will fail until the models are here; you can"
        say "fetch them from the studio's setup screen instead."
    fi
fi

# --- the interface is a build artefact, not in the repository ---------------
if [ ! -d web ]; then
    say "Building the studio interface (first run only)..."
    command -v npm >/dev/null 2>&1 || fail "npm is not installed, and web/ has not been built."
    ( cd apps/studio && npm install --silent && npm run build >/dev/null ) \
        || fail "The interface failed to build. Run it by hand:
    cd apps/studio && npm install && npm run build"
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

# --- the desktop window ------------------------------------------------------
# This is a desktop application. There is no browser mode: the studio runs on
# this machine, and it opens as a window on this machine. If the window has
# not been built yet, build it - that needs the Rust toolchain, which is the
# one thing beyond Python and ffmpeg the desktop app depends on.
APP_BUNDLE="apps/studio/src-tauri/target/release/bundle/macos/Multiva Studio.app"
APP_LINUX="apps/studio/src-tauri/target/release/multiva-studio"
DESKTOP=""
[ -d "$APP_BUNDLE" ] && DESKTOP="$APP_BUNDLE"
[ -z "$DESKTOP" ] && [ -x "$APP_LINUX" ] && DESKTOP="$APP_LINUX"

if [ -z "$DESKTOP" ]; then
    command -v cargo >/dev/null 2>&1 || fail "The desktop window has not been built, and Rust is not installed.
Install it from https://rustup.rs, then start Multiva again and it will build itself.
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    say ""
    say "The desktop window has not been built yet. Building it takes a few"
    say "minutes the first time and happens once."
    say ""
    if ! confirm "Build it now?"; then
        fail "Nothing was changed. When you are ready:
    cd apps/studio && npx tauri build"
    fi
    ( cd apps/studio && npx --yes tauri build ) || fail "The build did not finish. The output above says why."
    [ -d "$APP_BUNDLE" ] && DESKTOP="$APP_BUNDLE"
    [ -z "$DESKTOP" ] && [ -x "$APP_LINUX" ] && DESKTOP="$APP_LINUX"
    [ -n "$DESKTOP" ] || fail "The build finished but produced no window. Check apps/studio/src-tauri/target/release."
    say "Window built."
fi

# --- do not fight something already on the port ----------------------------
if lsof -ti:"$PORT" >/dev/null 2>&1; then
    say "An engine is already running on port ${PORT}; opening the window on it."
    open "$DESKTOP" 2>/dev/null || "$DESKTOP" >/dev/null 2>&1 &
    exit 0
fi

# --- open the studio once the engine says it is ready ----------------------
(
    for _ in $(seq 1 300); do
        if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/api/boot" 2>/dev/null \
             | grep -q '"ready": *true'; then
            printf '\n  Engine ready. Opening the studio window.\n\n'
            open "$DESKTOP" 2>/dev/null || "$DESKTOP" >/dev/null 2>&1 &
            exit 0
        fi
        sleep 1
    done
) &

say "Starting the engine on port ${PORT}. Models load on first use."
say "Ctrl-C stops it."
printf '  %s\n\n' "------------------------------------------------"

cd engine
export MULTIVA_LLM_PROVIDER="$PROVIDER"
[ -n "$MODEL" ] && export MULTIVA_LLM_MODEL="$MODEL"
export PYTORCH_ENABLE_MPS_FALLBACK=1
export TOKENIZERS_PARALLELISM=false
exec "../${PY#./}" -m uvicorn app:app --port "$PORT"
