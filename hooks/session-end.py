"""
SessionEnd hook - compiles the remaining uncompiled conversation turns into KB updates.

Uses a rolling watermark (last_compile_turn from session-state) to only process
turns that haven't been compiled by a mid-session Stop hook trigger. If no
mid-session compilation occurred, this behaves like the old "last N turns" approach.

No API calls in this process. A headless `claude -p` subprocess handles
compilation asynchronously so the hook returns within the timeout.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Recursion guard
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import (
    ROOT,
    SCRIPTS_DIR,
    SUMMARY_FILE,
    load_state,
    read_running_summary,
)

DAILY_DIR = ROOT / "daily"
KNOWLEDGE_DIR = ROOT / "knowledge"

logging.basicConfig(
    filename=str(SCRIPTS_DIR / "session-end.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

MAX_CONTEXT_CHARS = 12_000
MIN_TURNS = 2


def extract_turns(transcript_path: Path, start_turn: int = 0) -> tuple[str, int]:
    """
    Extract conversation turns from start_turn index onwards, capped at MAX_CONTEXT_CHARS.

    start_turn is the watermark from the last mid-session compilation — turns before
    this index have already been compiled and should not be repeated.
    """
    turns: list[str] = []

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
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
            else:
                role = entry.get("role", "")
                content = entry.get("content", "")

            if role not in ("user", "assistant"):
                continue

            if isinstance(content, list):
                text_parts = [
                    block.get("text", "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                content = "\n".join(text_parts)

            if isinstance(content, str) and content.strip():
                label = "User" if role == "user" else "Assistant"
                turns.append(f"**{label}:** {content.strip()}")

    # Apply watermark — skip turns already compiled by mid-session triggers
    window = turns[start_turn:]
    text = "\n\n".join(window)

    if len(text) > MAX_CONTEXT_CHARS:
        text = text[-MAX_CONTEXT_CHARS:]
        boundary = text.find("\n\n**")
        if boundary > 0:
            text = text[boundary + 2:]

    return text, len(window)


def ensure_daily_log(today_str: str) -> Path:
    """Ensure today's daily log file exists, return its path."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DAILY_DIR / f"{today_str}.md"
    if not log_path.exists():
        log_path.write_text(f"# Daily Log: {today_str}\n\n", encoding="utf-8")
    return log_path


def main() -> None:
    try:
        raw = sys.stdin.read()
        try:
            hook_input: dict = json.loads(raw)
        except json.JSONDecodeError:
            fixed = re.sub(r'(?<!\\)\\(?!["\\])', r'\\\\', raw)
            hook_input = json.loads(fixed)
    except Exception as e:
        logging.error("Failed to parse stdin: %s", e)
        return

    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", "") or hook_input.get("working_directory", "")
    transcript_path_str = hook_input.get("transcript_path", "")

    logging.info("SessionEnd: session=%s project=%s", session_id, Path(cwd).name if cwd else "?")

    if not transcript_path_str:
        logging.info("SKIP: no transcript path")
        return

    transcript_path = Path(transcript_path_str)
    if not transcript_path.exists():
        logging.info("SKIP: transcript not found")
        return

    # Read watermark from session state — skip turns already compiled mid-session
    state = load_state()
    start_turn = state.get("last_compile_turn", 0)
    running_summary = read_running_summary()

    logging.info("Watermark: start_turn=%d", start_turn)

    try:
        turns_text, turn_count = extract_turns(transcript_path, start_turn=start_turn)
    except Exception as e:
        logging.error("Transcript read failed: %s", e)
        return

    if turn_count < MIN_TURNS:
        logging.info("SKIP: only %d uncompiled turns", turn_count)
        return

    logging.info("Extracted %d uncompiled turns, spawning background compilation", turn_count)
    spawn_kb_compilation(cwd, turns_text, start_turn, running_summary)


def spawn_kb_compilation(
    cwd: str,
    turns_text: str,
    start_turn: int,
    running_summary: str,
) -> None:
    """Spawn a background headless Claude session to update the KB from remaining turns."""
    today = datetime.now(timezone.utc).astimezone()
    today_str = today.strftime("%Y-%m-%d")
    time_str = today.strftime("%H:%M")
    project = Path(cwd).name if cwd else "unknown"
    daily_log_path = ensure_daily_log(today_str)

    prior = (
        f"\n## Context from previous compilations this session\n\n{running_summary}\n\n"
        if running_summary else ""
    )

    window_label = f"turns {start_turn}→end" if start_turn > 0 else "full session"

    prompt = (
        f"Automated session-end KB compilation — do not ask questions, just do the work.\n\n"
        f"Project: `{project}` | Date: {today_str} {time_str}\n"
        f"Daily log path: {daily_log_path}\n"
        f"KB root: {KNOWLEDGE_DIR}/\n"
        f"Summary output path: {SUMMARY_FILE}\n"
        f"{prior}"
        f"## Raw conversation turns ({window_label})\n\n"
        f"{turns_text}\n\n"
        f"## Instructions\n\n"
        f"### Step 1 — Append a session summary to the daily log\n"
        f"Append the following block to {daily_log_path}:\n"
        f"```\n"
        f"## Session {time_str} | {project}\n\n"
        f"<3-7 bullet points summarising what was discussed, decided, or built.\n"
        f" Focus on outcomes and decisions, not a step-by-step replay.\n"
        f" Each bullet is one concise sentence.>\n"
        f"```\n\n"
        f"### Step 2 — Update the KB\n"
        f"Read the existing KB articles for this project FIRST before deciding what to update.\n"
        f"Read: {KNOWLEDGE_DIR}/projects/{project}/ (list and read relevant files).\n\n"
        f"For each insight that is non-obvious and worth remembering in a future session:\n\n"
        f"#### Article structure rules\n"
        f"- Flat article under ~60 lines, single topic → update in place\n"
        f"- Flat article >80 lines OR 3+ distinct H2 sections → split into a topic directory:\n"
        f"  - `<topic>/_index.md`: 2-3 sentence overview + bullet list of leaf files\n"
        f"  - Leaf files: 20-50 lines each, self-contained, wikilinks to siblings and _index\n"
        f"  - Remove the old flat file once the directory is created\n"
        f"- Topic already has a directory → update the right leaf; update `_index.md` if scope changed\n\n"
        f"#### Index format\n"
        f"Update {KNOWLEDGE_DIR}/index.md. For topic directories, one collapsed row:\n"
        f"  `| [[projects/{project}/<topic>/_index]] | leaf1, leaf2, leaf3 | {project} | {today_str} |`\n"
        f"Flat articles remain as individual rows.\n\n"
        f"#### Log entry\n"
        f"Append one line to {KNOWLEDGE_DIR}/log.md:\n"
        f"  `## {today_str}T{time_str} compiled | {project} — N articles updated`\n\n"
        f"IMPORTANT: Actually write the files using your file-writing tools.\n"
        f"Do NOT just describe what you would do — execute it.\n"
        f"Skip: ephemeral task details, commands run, anything already identical in existing KB.\n"
        f"Only skip Step 2 if every insight is ALREADY captured verbatim in the existing KB.\n"
        f"If you skip Step 2, still append one line to {KNOWLEDGE_DIR}/log.md:\n"
        f"  `## {today_str}T{time_str} compiled | {project} — no changes needed`\n"
        f"Today's date: {today_str}."
    )

    env = {**os.environ, "CLAUDE_INVOKED_BY": "session-end-hook"}

    stdout_log = SCRIPTS_DIR / "kb-compile-stdout.log"
    stderr_log = SCRIPTS_DIR / "kb-compile-stderr.log"
    separator = f"\n\n{'='*60}\n{today_str} {time_str} | {project} [{window_label}]\n{'='*60}\n"
    try:
        with open(stdout_log, "a") as f_out, open(stderr_log, "a") as f_err:
            f_out.write(separator)
            f_err.write(separator)
        subprocess.Popen(
            ["claude", "--dangerously-skip-permissions", "--model", "claude-sonnet-4-6", "-p", prompt],
            env=env,
            cwd=str(ROOT),
            stdout=open(stdout_log, "a"),
            stderr=open(stderr_log, "a"),
            start_new_session=True,
        )
        logging.info(
            "Spawned background KB compilation: project=%s start_turn=%d", project, start_turn
        )
    except FileNotFoundError:
        logging.warning("claude CLI not found in PATH — KB compilation skipped")
    except Exception as e:
        logging.error("Failed to spawn KB compilation: %s", e)


if __name__ == "__main__":
    main()
