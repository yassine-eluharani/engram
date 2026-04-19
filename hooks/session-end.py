"""
SessionEnd hook - appends raw conversation turns to today's daily log,
then spawns a background Claude process to compile the log into KB updates.

No API calls in this process. A headless `claude -p` subprocess handles
the compilation asynchronously so the hook returns within the timeout.
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

    try:
        turns_text, turn_count = extract_turns(transcript_path)
    except Exception as e:
        logging.error("Transcript read failed: %s", e)
        return

    if turn_count < MIN_TURNS:
        logging.info("SKIP: only %d turns", turn_count)
        return

    logging.info("Extracted %d turns, spawning background compilation", turn_count)
    spawn_kb_compilation(cwd, turns_text)


def spawn_kb_compilation(cwd: str, turns_text: str) -> None:
    """Spawn a background headless Claude session to summarise the session and update the KB."""
    today = datetime.now(timezone.utc).astimezone()
    today_str = today.strftime("%Y-%m-%d")
    time_str = today.strftime("%H:%M")
    project = Path(cwd).name if cwd else "unknown"
    knowledge_dir = ROOT / "knowledge"
    daily_log_path = ensure_daily_log(today_str)

    prompt = (
        f"Automated session-end task — do not ask questions, just do the work.\n\n"
        f"Project: `{project}` | Date: {today_str} {time_str}\n"
        f"Daily log path: {daily_log_path}\n"
        f"KB root: {knowledge_dir}/\n\n"
        f"## Raw conversation turns from this session\n\n"
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
        f"For each insight that is non-obvious and worth remembering in a future session:\n"
        f"- Update or create the relevant article under {knowledge_dir}/projects/{project}/\n"
        f"- Follow Obsidian frontmatter format (title, project, tags, created, updated)\n"
        f"- Update {knowledge_dir}/index.md (add/update the row for any changed article)\n"
        f"- Append one line to {knowledge_dir}/log.md:\n"
        f"  `## {today_str}T{time_str} compiled | {project} — N articles updated`\n\n"
        f"Skip: ephemeral task details, commands run, anything already evident from the code.\n"
        f"If nothing is worth saving to the KB, skip Step 2 entirely — that is fine.\n"
        f"Today's date: {today_str}."
    )

    env = {**os.environ, "CLAUDE_INVOKED_BY": "session-end-hook"}

    stderr_log = SCRIPTS_DIR / "kb-compile-stderr.log"
    try:
        subprocess.Popen(
            ["claude", "--dangerously-skip-permissions", "--model", "claude-haiku-4-5-20251001", "-p", prompt],
            env=env,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=open(stderr_log, "a"),
            start_new_session=True,  # detach so hook process can exit
        )
        logging.info("Spawned background KB compilation for project=%s", project)
    except FileNotFoundError:
        logging.warning("claude CLI not found in PATH — KB compilation skipped")
    except Exception as e:
        logging.error("Failed to spawn KB compilation: %s", e)


if __name__ == "__main__":
    main()
