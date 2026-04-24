"""
Stop hook - mid-session compilation trigger.

Evaluates thresholds after each Claude turn. Fires rolling-window compilation when:
  - edits_since_compile >= 8   (meaningful chunk of file work done)
  - turns_since_compile >= 15  AND  time_since_last_compile >= 10 min

Uses a lockfile to prevent concurrent compilations. After a successful spawn the
lock is intentionally left in place (~5 min TTL) to prevent rapid re-triggering.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Recursion guard — this hook is called by the hook runner, not by a compiled session
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import (
    ROOT,
    SCRIPTS_DIR,
    acquire_lock,
    load_state,
    minutes_since,
    read_running_summary,
    release_lock,
    reset_compile_counters,
    save_state,
)

KNOWLEDGE_DIR = ROOT / "knowledge"
SUMMARY_FILE = SCRIPTS_DIR / "last-compile-summary.txt"

EDITS_THRESHOLD = 8
TURNS_THRESHOLD = 15
TIME_THRESHOLD_MINUTES = 10
MAX_CONTEXT_CHARS = 12_000

logging.basicConfig(
    filename=str(SCRIPTS_DIR / "stop-hook.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def extract_turns_window(transcript_path: Path, start_turn: int) -> tuple[str, int]:
    """Extract conversation turns from start_turn index onwards, capped at MAX_CONTEXT_CHARS."""
    turns: list[str] = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = entry.get("message", {})
                role = msg.get("role", "") if isinstance(msg, dict) else entry.get("role", "")
                content = msg.get("content", "") if isinstance(msg, dict) else entry.get("content", "")
                if role not in ("user", "assistant"):
                    continue
                if isinstance(content, list):
                    content = "\n".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                if isinstance(content, str) and content.strip():
                    label = "User" if role == "user" else "Assistant"
                    turns.append(f"**{label}:** {content.strip()}")
    except Exception as e:
        logging.error("Transcript read error: %s", e)
        return "", 0

    window = turns[start_turn:]
    text = "\n\n".join(window)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[-MAX_CONTEXT_CHARS:]
        boundary = text.find("\n\n**")
        if boundary > 0:
            text = text[boundary + 2:]
    return text, len(window)


def spawn_compilation(state: dict, current_turn: int) -> bool:
    """Spawn a background Claude process to compile the current window. Returns True if spawned."""
    transcript_path_str = state.get("transcript_path", "")
    cwd = state.get("cwd", "")
    start_turn = state.get("last_compile_turn", 0)
    running_summary = read_running_summary()

    if not transcript_path_str:
        logging.info("SKIP: no transcript_path in session state")
        return False
    transcript_path = Path(transcript_path_str)
    if not transcript_path.exists():
        logging.info("SKIP: transcript not found at %s", transcript_path_str)
        return False

    turns_text, count = extract_turns_window(transcript_path, start_turn)
    if count < 2:
        logging.info("SKIP: only %d new turns since last compile", count)
        return False

    today = datetime.now(timezone.utc).astimezone()
    today_str = today.strftime("%Y-%m-%d")
    time_str = today.strftime("%H:%M")
    project = Path(cwd).name if cwd else "unknown"

    prior = (
        f"\n## Context from previous compilations this session\n\n{running_summary}\n\n"
        if running_summary else ""
    )

    prompt = (
        f"Automated mid-session KB compilation — do not ask questions, just do the work.\n\n"
        f"Project: `{project}` | Date: {today_str} {time_str}\n"
        f"KB root: {KNOWLEDGE_DIR}/\n"
        f"Summary output path: {SUMMARY_FILE}\n"
        f"{prior}"
        f"## Conversation turns (window {start_turn}→{current_turn})\n\n"
        f"{turns_text}\n\n"
        f"## Instructions\n\n"
        f"Read the existing KB articles for this project FIRST, then update.\n"
        f"Path: {KNOWLEDGE_DIR}/projects/{project}/\n\n"
        f"### Article structure rules\n"
        f"- Flat article under ~60 lines, single topic → update in place\n"
        f"- Flat article >80 lines OR 3+ distinct H2 sections → split into a topic directory:\n"
        f"  - `<topic>/_index.md`: 2-3 sentence overview + bullet list of leaf files with one-line descriptions\n"
        f"  - Leaf files: 20-50 lines each, self-contained, wikilinks to siblings and _index\n"
        f"  - Remove the old flat file once the directory is created\n"
        f"- Topic already has a directory → update the right leaf; update `_index.md` if scope changed\n\n"
        f"### Index format\n"
        f"Update {KNOWLEDGE_DIR}/index.md. For topic directories, one collapsed row:\n"
        f"  `| [[projects/{project}/<topic>/_index]] | leaf1, leaf2, leaf3 | {project} | {today_str} |`\n"
        f"Flat articles remain as individual rows.\n\n"
        f"### After completing KB updates\n"
        f"1. Write a 2-3 sentence summary to {SUMMARY_FILE} (what was compiled — used by next window)\n"
        f"2. Append one line to {KNOWLEDGE_DIR}/log.md:\n"
        f"   `## {today_str}T{time_str} mid-session | {project} — N articles updated`\n\n"
        f"IMPORTANT: Actually write the files using your tools. Do NOT just describe what to do.\n"
        f"Skip: ephemeral task details, commands run, anything already identical in existing KB.\n"
        f"Today's date: {today_str}."
    )

    env = {**os.environ, "CLAUDE_INVOKED_BY": "stop-hook-mid-session"}
    stdout_log = SCRIPTS_DIR / "kb-compile-stdout.log"
    stderr_log = SCRIPTS_DIR / "kb-compile-stderr.log"
    sep = f"\n\n{'='*60}\n{today_str} {time_str} | {project} [mid-session]\n{'='*60}\n"

    try:
        with open(stdout_log, "a") as fo, open(stderr_log, "a") as fe:
            fo.write(sep)
            fe.write(sep)
        subprocess.Popen(
            ["claude", "--dangerously-skip-permissions", "--model", "claude-sonnet-4-6", "-p", prompt],
            env=env,
            cwd=str(ROOT),
            stdout=open(stdout_log, "a"),
            stderr=open(stderr_log, "a"),
            start_new_session=True,
        )
        logging.info(
            "Spawned mid-session compile: project=%s turns=%d→%d", project, start_turn, current_turn
        )
        return True
    except FileNotFoundError:
        logging.warning("claude CLI not found — mid-session compilation skipped")
        return False
    except Exception as e:
        logging.error("Spawn failed: %s", e)
        return False


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    # Built-in recursion guard from Claude Code
    if payload.get("stop_hook_active"):
        sys.exit(0)

    state = load_state()
    state["turns_since_compile"] = state.get("turns_since_compile", 0) + 1
    save_state(state)

    edits = state.get("edits_since_compile", 0)
    turns = state["turns_since_compile"]
    elapsed = minutes_since(state.get("last_compile_time", ""))
    current_turn = state.get("last_compile_turn", 0) + turns

    edits_trigger = edits >= EDITS_THRESHOLD
    turns_trigger = turns >= TURNS_THRESHOLD and elapsed >= TIME_THRESHOLD_MINUTES

    if not (edits_trigger or turns_trigger):
        return

    logging.info(
        "Threshold met — edits=%d turns=%d elapsed=%.1fmin edits_trigger=%s turns_trigger=%s",
        edits, turns, elapsed, edits_trigger, turns_trigger,
    )

    if not acquire_lock():
        logging.info("Lock held by another process — skipping this trigger")
        return

    spawned = spawn_compilation(state, current_turn)
    if spawned:
        state = load_state()
        state = reset_compile_counters(state, current_turn)
        save_state(state)
        # Lock intentionally NOT released — expires after 5 min to prevent rapid re-triggering
    else:
        release_lock()  # Release immediately on failure so the next trigger can retry


if __name__ == "__main__":
    main()
