#!/bin/bash
# Double-click launcher for Finder.
#
# Everything it does lives in run.sh; this only exists so the app can be
# started without opening a terminal. Two launchers that drift apart is worse
# than one, so this one delegates rather than duplicating.

cd "$(dirname "$0")" || exit 1
exec ./run.sh "$@"
