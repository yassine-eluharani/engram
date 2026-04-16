"""
SessionEnd hook - appends raw conversation turns to today's daily log.

No API calls. Extracts the last N turns from the transcript JSONL and
appends them to daily/YYYY-MM-DD.md for Claude to process next session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Recursion guard
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "daily"
SCRIPTS_DIR = ROOT / "scripts"

logging.basicConfig(
    filename=str(SCRIPTS_DIR / "session-end.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

MAX_TURNS = 30
MAX_CONTEXT_CHARS = 12_000
MIN_TURNS = 2


def extract_turns(transcript_path: Path) -> tuple[str, int]:
    """Extract last N conversation turns from JSONL transcript."""
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

    recent = turns[-MAX_TURNS:]
    text = "\n\n".join(recent)

    if len(text) > MAX_CONTEXT_CHARS:
        text = text[-MAX_CONTEXT_CHARS:]
        boundary = text.find("\n\n**")
        if boundary > 0:
            text = text[boundary + 2:]

    return text, len(recent)


def append_to_daily_log(session_id: str, cwd: str, turns_text: str) -> None:
    """Append session turns to today's daily log."""
    today = datetime.now(timezone.utc).astimezone()
    log_path = DAILY_DIR / f"{today.strftime('%Y-%m-%d')}.md"

    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    if not log_path.exists():
        log_path.write_text(
            f"# Daily Log: {today.strftime('%Y-%m-%d')}\n\n",
            encoding="utf-8",
        )

    time_str = today.strftime("%H:%M")
    project = Path(cwd).name if cwd else "unknown"
    entry = f"\n## Session {time_str} | {project}\n\n{turns_text}\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


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

    try:
        turns_text, turn_count = extract_turns(transcript_path)
    except Exception as e:
        logging.error("Transcript read failed: %s", e)
        return

    if turn_count < MIN_TURNS:
        logging.info("SKIP: only %d turns", turn_count)
        return

    append_to_daily_log(session_id, cwd, turns_text)
    logging.info("Logged %d turns to daily log", turn_count)


if __name__ == "__main__":
    main()
