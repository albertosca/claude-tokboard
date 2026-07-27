#!/usr/bin/env bash
# Auto-provisions the TUI dashboard's dependencies (textual +
# textual-plotext) into this plugin's own .venv, once. Every command
# invocation after the first one is a fast no-op (just checks the venv
# exists and has textual importable) - see
# docs/superpowers/specs/2026-07-27-plugin-publication-design.md for why
# this is auto-setup rather than a manual documented step.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PLUGIN_ROOT/.venv"

if [ -x "$VENV_DIR/bin/python3" ] && "$VENV_DIR/bin/python3" -c "import textual" 2>/dev/null; then
  exit 0
fi

echo "Setting up the TUI dashboard's dependencies (textual, textual-plotext) - this only happens once."

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 not found on PATH. Install Python 3 to use the TUI dashboard." >&2
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
