"""
PostToolUse hook - lightweight edit counter for mid-session compilation trigger.

Increments edits_since_compile whenever a Write, Edit, or NotebookEdit tool
completes successfully. Under 50ms, no API calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import load_state, save_state

EDIT_TOOLS = {"Write", "Edit", "NotebookEdit"}


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return

    tool_name = payload.get("tool_name", "") or payload.get("tool", "")
    if tool_name not in EDIT_TOOLS:
        return

    state = load_state()
    state["edits_since_compile"] = state.get("edits_since_compile", 0) + 1
    save_state(state)


if __name__ == "__main__":
    main()
