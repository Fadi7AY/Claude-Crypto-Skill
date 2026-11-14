#!/usr/bin/env bash
# Copy the skills into a Claude Code skills folder.
#   ./install.sh            -> ./.claude/skills of the current project
#   ./install.sh --global   -> ~/.claude/skills
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills"
DEST="$PWD/.claude/skills"
[ "${1:-}" = "--global" ] && DEST="$HOME/.claude/skills"
mkdir -p "$DEST"
cp -R "$SRC"/. "$DEST"/
find "$DEST" -name "__pycache__" -type d -prune -exec rm -rf {} +
echo "Installed to $DEST"
echo "Next: pip install -r $(dirname "$SRC")/requirements.txt"
