#!/usr/bin/env python3
"""
Cross-platform Engram installer (Windows, macOS, Linux).

    python install.py [--install-dir PATH]

Does everything install.sh does, portably:
  1. Copies Engram to the install dir (default ~/.claude/engram)
  2. Runs `uv sync` for Python dependencies
  3. Merges the four hooks into ~/.claude/settings.json (backup written first)
  4. Appends the KB instructions to ~/.claude/CLAUDE.md
  5. Installs the /garden slash command

Hook commands are written with absolute quoted paths — no `~` — so they work
in cmd/PowerShell as well as POSIX shells.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"

FILES = [
    "hooks/session-start.py",
    "hooks/session-end.py",
    "hooks/stop.py",
    "hooks/post-tool-use.py",
    "hooks/shared.py",
    "scripts/config.py",
    "scripts/utils.py",
    "scripts/lint.py",
    "scripts/garden.py",
    "AGENTS.md",
    "pyproject.toml",
]

KB_DIRS = [
    "knowledge/concepts",
    "knowledge/projects",
    "knowledge/qa",
    "daily",
    "reports",
]

INDEX_STUB = (
    "# Knowledge Base Index\n\n"
    "| Article | Summary | Project | Updated |\n"
    "|---------|---------|---------|---------|\n"
)

LOG_STUB = (
    "# Knowledge Base Log\n\n"
    "Append-only record of all KB operations.\n"
    "Format: `## [TIMESTAMP] operation | details`\n\n"
)


def hook_entry(install_dir: Path, script: str, timeout: int, matcher: str = "") -> dict:
    cmd = (
        f'uv run --directory "{install_dir}" '
        f'python "{install_dir / "hooks" / script}"'
    )
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": cmd, "timeout": timeout}],
    }


def merge_hooks(settings_path: Path, install_dir: Path) -> str:
    """Merge Engram's hooks into settings.json. Returns a status message."""
    settings: dict = {}
    if settings_path.exists():
        text = settings_path.read_text(encoding="utf-8")
        if "engram" in text or "memory-compiler" in text:
            return "hooks already present in settings.json — left untouched"
        settings = json.loads(text)
        shutil.copy2(settings_path, settings_path.with_suffix(".json.bak"))

    hooks = settings.setdefault("hooks", {})
    new = {
        "SessionStart": hook_entry(install_dir, "session-start.py", 15),
        "SessionEnd": hook_entry(install_dir, "session-end.py", 10),
        "Stop": hook_entry(install_dir, "stop.py", 10),
        "PostToolUse": hook_entry(install_dir, "post-tool-use.py", 5, "Write|Edit|NotebookEdit"),
    }
    for event, entry in new.items():
        hooks.setdefault(event, []).append(entry)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return "hooks merged into settings.json (backup: settings.json.bak)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Engram")
    parser.add_argument("--install-dir", default=str(CLAUDE_DIR / "engram"))
    args = parser.parse_args()
    install_dir = Path(args.install_dir).expanduser().resolve()

    print(f"\nInstalling Engram to: {install_dir}\n")

    if not shutil.which("uv"):
        print("ERROR: 'uv' is required — https://docs.astral.sh/uv/getting-started/installation/")
        return 1

    # 1. Copy files + create KB skeleton
    for rel in FILES:
        dest = install_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / rel, dest)
    for rel in KB_DIRS:
        (install_dir / rel).mkdir(parents=True, exist_ok=True)

    index = install_dir / "knowledge" / "index.md"
    if not index.exists():
        index.write_text(INDEX_STUB, encoding="utf-8")
    log = install_dir / "knowledge" / "log.md"
    if not log.exists():
        log.write_text(LOG_STUB, encoding="utf-8")
    print("  * files copied")

    # 2. Dependencies
    subprocess.run(["uv", "sync", "--directory", str(install_dir), "--quiet"], check=True)
    print("  * dependencies installed")

    # 3. Hooks
    print(f"  * {merge_hooks(CLAUDE_DIR / 'settings.json', install_dir)}")

    # 4. CLAUDE.md instructions
    claude_md = CLAUDE_DIR / "CLAUDE.md"
    engram_md = (SRC / "CLAUDE.md").read_text(encoding="utf-8")
    if not claude_md.exists():
        claude_md.write_text(engram_md, encoding="utf-8")
        print("  * created ~/.claude/CLAUDE.md")
    elif "memory-compiler" in claude_md.read_text(encoding="utf-8") or "Knowledge Base" in claude_md.read_text(encoding="utf-8"):
        print("  * CLAUDE.md already contains KB instructions — left untouched")
    else:
        with open(claude_md, "a", encoding="utf-8") as f:
            f.write("\n" + engram_md)
        print("  * appended KB instructions to ~/.claude/CLAUDE.md")

    # 5. /garden command
    commands_dir = CLAUDE_DIR / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    garden = (SRC / "commands" / "garden.md").read_text(encoding="utf-8")
    (commands_dir / "garden.md").write_text(
        garden.replace("__INSTALL_DIR__", str(install_dir)), encoding="utf-8"
    )
    print("  * /garden command installed")

    print(
        f"\nDone. Restart Claude Code to activate.\n"
        f"  Knowledge base: {install_dir / 'knowledge'}\n"
        f"  Obsidian vault: open that folder as a vault\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
