#!/usr/bin/env bash
# Engram installer
# Installs Engram globally so it works in every Claude Code session.
# Usage: ./install.sh [--install-dir <path>]

set -e

ENGRAM_DEFAULT="$HOME/.claude/engram"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"

# ── Parse args ────────────────────────────────────────────────────────
INSTALL_DIR="$ENGRAM_DEFAULT"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo ""
echo "  ███████╗███╗   ██╗ ██████╗ ██████╗  █████╗ ███╗   ███╗"
echo "  ██╔════╝████╗  ██║██╔════╝ ██╔══██╗██╔══██╗████╗ ████║"
echo "  █████╗  ██╔██╗ ██║██║  ███╗██████╔╝███████║██╔████╔██║"
echo "  ██╔══╝  ██║╚██╗██║██║   ██║██╔══██╗██╔══██║██║╚██╔╝██║"
echo "  ███████╗██║ ╚████║╚██████╔╝██║  ██║██║  ██║██║ ╚═╝ ██║"
echo "  ╚══════╝╚═╝  ╚═══╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝"
echo ""
echo "  Persistent project-aware memory for Claude Code"
echo "  Installing to: $INSTALL_DIR"
echo ""

# ── Check prerequisites ───────────────────────────────────────────────
check() {
  if ! command -v "$1" &>/dev/null; then
    echo "  ERROR: '$1' is required but not found."
    echo "  $2"
    exit 1
  fi
}

check uv    "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
check python3 "Install Python 3.12+: https://www.python.org/downloads/"

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'; then
  echo "  ✓ Python $PYTHON_VERSION"
else
  echo "  ERROR: Python 3.12+ required (found $PYTHON_VERSION)"
  exit 1
fi

echo "  ✓ uv $(uv --version | awk '{print $2}')"

# ── Copy files ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "  Copying files..."

mkdir -p \
  "$INSTALL_DIR/hooks" \
  "$INSTALL_DIR/scripts" \
  "$INSTALL_DIR/knowledge/concepts" \
  "$INSTALL_DIR/knowledge/projects" \
  "$INSTALL_DIR/knowledge/qa" \
  "$INSTALL_DIR/daily" \
  "$INSTALL_DIR/reports"

cp "$SCRIPT_DIR/hooks/session-start.py"  "$INSTALL_DIR/hooks/"
cp "$SCRIPT_DIR/hooks/session-end.py"    "$INSTALL_DIR/hooks/"
cp "$SCRIPT_DIR/scripts/config.py"       "$INSTALL_DIR/scripts/"
cp "$SCRIPT_DIR/scripts/utils.py"        "$INSTALL_DIR/scripts/"
cp "$SCRIPT_DIR/scripts/lint.py"         "$INSTALL_DIR/scripts/"
cp "$SCRIPT_DIR/AGENTS.md"               "$INSTALL_DIR/"
cp "$SCRIPT_DIR/pyproject.toml"          "$INSTALL_DIR/"

# Initialize empty KB files if they don't exist yet
[ -f "$INSTALL_DIR/knowledge/index.md" ] || cat > "$INSTALL_DIR/knowledge/index.md" << 'EOF'
# Knowledge Base Index

| Article | Summary | Project | Updated |
|---------|---------|---------|---------|
EOF

[ -f "$INSTALL_DIR/knowledge/log.md" ] || cat > "$INSTALL_DIR/knowledge/log.md" << 'EOF'
# Knowledge Base Log

Append-only record of all KB operations.
Format: `## [TIMESTAMP] operation | details`

EOF

# ── Install Python dependencies ───────────────────────────────────────
echo ""
echo "  Installing Python dependencies..."
uv sync --directory "$INSTALL_DIR" --quiet
echo "  ✓ Dependencies installed"

# ── Patch hooks to use absolute INSTALL_DIR ───────────────────────────
# (hooks need to find ROOT even when run from any working directory;
#  the ROOT detection via __file__ already handles this correctly)

# ── Update ~/.claude/settings.json ───────────────────────────────────
echo ""
echo "  Updating Claude Code settings..."

HOOK_START="uv run --directory $INSTALL_DIR python $INSTALL_DIR/hooks/session-start.py"
HOOK_END="uv run --directory $INSTALL_DIR python $INSTALL_DIR/hooks/session-end.py"

if [ ! -f "$CLAUDE_SETTINGS" ]; then
  cat > "$CLAUDE_SETTINGS" << EOF
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [{"type": "command", "command": "$HOOK_START", "timeout": 15}]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [{"type": "command", "command": "$HOOK_END", "timeout": 10}]
      }
    ]
  }
}
EOF
  echo "  ✓ Created $CLAUDE_SETTINGS"
else
  # settings.json exists — check if hooks are already present
  if grep -q "engram\|memory-compiler" "$CLAUDE_SETTINGS" 2>/dev/null; then
    echo "  ⚠ Engram hooks already detected in settings.json"
    echo "    Edit $CLAUDE_SETTINGS manually if you need to update the paths."
  else
    echo ""
    echo "  ┌─────────────────────────────────────────────────────────────┐"
    echo "  │ ACTION REQUIRED: Add hooks to ~/.claude/settings.json       │"
    echo "  │                                                             │"
    echo "  │ Add these entries inside the \"hooks\" object:               │"
    echo "  │                                                             │"
    echo "  │   \"SessionStart\": [{                                       │"
    echo "  │     \"matcher\": \"\",                                        │"
    echo "  │     \"hooks\": [{                                            │"
    echo "  │       \"type\": \"command\",                                  │"
    echo "  │       \"timeout\": 15,                                       │"
    echo "  │       \"command\": \"$HOOK_START\"                            │"
    echo "  │     }]                                                      │"
    echo "  │   }],                                                       │"
    echo "  │   \"SessionEnd\": [{                                         │"
    echo "  │     \"matcher\": \"\",                                        │"
    echo "  │     \"hooks\": [{                                            │"
    echo "  │       \"type\": \"command\",                                  │"
    echo "  │       \"timeout\": 10,                                       │"
    echo "  │       \"command\": \"$HOOK_END\"                              │"
    echo "  │     }]                                                      │"
    echo "  │   }]                                                        │"
    echo "  └─────────────────────────────────────────────────────────────┘"
  fi
fi

# ── Update ~/.claude/CLAUDE.md ────────────────────────────────────────
ENGRAM_CLAUDE_MD="$SCRIPT_DIR/CLAUDE.md"
if [ -f "$ENGRAM_CLAUDE_MD" ]; then
  if [ ! -f "$CLAUDE_MD" ]; then
    cp "$ENGRAM_CLAUDE_MD" "$CLAUDE_MD"
    echo "  ✓ Created $CLAUDE_MD"
  elif grep -q "Engram\|memory-compiler\|memory system" "$CLAUDE_MD" 2>/dev/null; then
    echo "  ✓ CLAUDE.md already contains Engram instructions"
  else
    echo "" >> "$CLAUDE_MD"
    cat "$ENGRAM_CLAUDE_MD" >> "$CLAUDE_MD"
    echo "  ✓ Appended Engram instructions to $CLAUDE_MD"
  fi
fi

# ── Done ─────────────────────────────────────────────────────────────
echo ""
echo "  ✓ Engram installed successfully!"
echo ""
echo "  Knowledge base: $INSTALL_DIR/knowledge/"
echo "  Daily logs:     $INSTALL_DIR/daily/"
echo "  Obsidian vault: open $INSTALL_DIR/knowledge/ as a vault"
echo ""
echo "  Restart Claude Code to activate."
echo ""
