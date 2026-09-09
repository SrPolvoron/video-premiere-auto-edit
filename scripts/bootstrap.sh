#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if [ ! -x .venv/bin/python ]; then
    "$PYTHON" -m venv .venv
fi
# Optional extras can be supplied as: sh scripts/bootstrap.sh audio,dev
SPEC="."
if [ "$#" -gt 0 ]; then
    case "$1" in audio|dev|audio,dev|dev,audio) SPEC=".[$1]" ;; *) echo "Use audio, dev or audio,dev" >&2; exit 2 ;; esac
fi
.venv/bin/python -m pip install --disable-pip-version-check -e "$SPEC"
.venv/bin/autoeditor doctor
printf '%s\n' 'Next: .venv/bin/autoeditor demo work/smoke'
