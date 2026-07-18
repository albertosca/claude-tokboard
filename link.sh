#!/usr/bin/env bash
# Symlink this project's hooks/commands into ~/.claude/.
# Idempotente: backup de arquivos/diretórios pré-existentes que não sejam symlinks já corretos.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_SUFFIX=".claude-metrics-dashboard.bak"

link() {
  local src="$1" dest="$2"
  if [[ -L "$dest" ]]; then
    local current
    current=$(readlink "$dest")
    if [[ "$current" == "$src" ]]; then
      echo "ok   $dest → $src"
      return
    fi
    echo "diff $dest já aponta para $current; trocando por $src"
    rm "$dest"
  elif [[ -e "$dest" ]]; then
    echo "back $dest existe → movendo para ${dest}${BACKUP_SUFFIX}"
    mv "$dest" "${dest}${BACKUP_SUFFIX}"
  fi
  mkdir -p "$(dirname "$dest")"
  ln -s "$src" "$dest"
  echo "link $dest → $src"
}

link "$ROOT/hooks/metrics-log.py"    "$HOME/.claude/hooks/metrics-log.py"
link "$ROOT/hooks/metrics-report.py" "$HOME/.claude/hooks/metrics-report.py"
link "$ROOT/hooks/metrics-tui.py"    "$HOME/.claude/hooks/metrics-tui.py"
link "$ROOT/commands/metrics.md"     "$HOME/.claude/commands/metrics.md"
link "$ROOT/commands/metrics-tui.md" "$HOME/.claude/commands/metrics-tui.md"
