#!/usr/bin/env bash
# Auto-provisions the TUI dashboard's dependencies (textual +
# textual-plotext) into this plugin's own .venv, once. Every command
# invocation after the first one is a fast no-op (just checks the venv
# exists and has textual importable) - see
# docs/superpowers/specs/2026-07-27-plugin-publication-design.md for why
# this is auto-setup rather than a manual documented step.
#
# dashboard_tui.py needs Python 3.10+ (it evaluates `str | tuple[...]`
# type unions at import time and uses `zip(strict=)`), but a stock macOS
# still ships python3 3.9.6, and textual>=8 itself accepts 3.9 - so
# nothing upstream catches this for us. Every path that could hand back a
# python3 (the fast-exit venv, and the interpreter on PATH used to build a
# fresh one) is version-checked below.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PLUGIN_ROOT/.venv"
MIN_PY_MAJOR=3
MIN_PY_MINOR=10

# Prints "<major> <minor>" for the python3 binary at $1, or fails if its
# `--version` output can't be parsed as such.
python_major_minor() {
  local out major rest minor
  out="$("$1" --version 2>&1)" || return 1
  out="${out#Python }"
  major="${out%%.*}"
  rest="${out#*.}"
  minor="${rest%%.*}"
  case "$major" in '' | *[!0-9]*) return 1 ;; esac
  case "$minor" in '' | *[!0-9]*) return 1 ;; esac
  echo "$major $minor"
}

python_is_new_enough() {
  local mm major minor
  mm="$(python_major_minor "$1")" || return 1
  major="${mm%% *}"
  minor="${mm#* }"
  if [ "$major" -gt "$MIN_PY_MAJOR" ]; then
    return 0
  fi
  [ "$major" -eq "$MIN_PY_MAJOR" ] && [ "$minor" -ge "$MIN_PY_MINOR" ]
}

if [ -x "$VENV_DIR/bin/python3" ]; then
  if ! python_is_new_enough "$VENV_DIR/bin/python3"; then
    echo "The TUI virtual environment at $VENV_DIR was built with an old Python ($("$VENV_DIR/bin/python3" --version 2>&1)); the TUI dashboard needs Python $MIN_PY_MAJOR.$MIN_PY_MINOR+. Removing it so it gets rebuilt with a newer interpreter." >&2
    rm -rf "$VENV_DIR"
  elif "$VENV_DIR/bin/python3" -c "import textual" 2>/dev/null; then
    exit 0
  fi
fi

echo "Setting up the TUI dashboard's dependencies (textual, textual-plotext) - this only happens once."

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 not found on PATH. Install Python 3 to use the TUI dashboard." >&2
  exit 1
fi

if ! python_is_new_enough python3; then
  echo "Error: the TUI dashboard needs Python $MIN_PY_MAJOR.$MIN_PY_MINOR or newer, but python3 on PATH is $(python3 --version 2>&1). Install Python $MIN_PY_MAJOR.$MIN_PY_MINOR+ and make sure it comes first on PATH (a version manager like pyenv or asdf can do this), then try again." >&2
  exit 1
fi

if ! python3 -m venv "$VENV_DIR" 2>/tmp/setup-tui-venv-error.log; then
  echo "Error: failed to create the virtual environment at $VENV_DIR" >&2
  cat /tmp/setup-tui-venv-error.log >&2
  rm -f /tmp/setup-tui-venv-error.log
  exit 1
fi
rm -f /tmp/setup-tui-venv-error.log

if ! "$VENV_DIR/bin/pip" install -q -r "$PLUGIN_ROOT/requirements-tui.txt"; then
  echo "Error: failed to install TUI dependencies into $VENV_DIR" >&2
  exit 1
fi

echo "Done - the TUI dashboard is ready."
