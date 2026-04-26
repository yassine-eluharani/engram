"""
Shared helpers for Engram hooks.

- session-state.json: tracks activity within the current session
- compile.lock: prevents concurrent background compilations
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
STATE_FILE = SCRIPTS_DIR / "session-state.json"
LOCK_FILE = SCRIPTS_DIR / "compile.lock"
SUMMARY_FILE = SCRIPTS_DIR / "last-compile-summary.txt"

# How old a lockfile must be (seconds) before we consider the process dead
LOCK_STALE_SECONDS = 180  # 3 minutes

# Single, append-only feed of automatic-update activity. Easy to tail.
AUTO_LOG_FILE = ROOT / "auto-updates.log"


# ── Session state ─────────────────────────────────────────────────────────────

DEFAULT_STATE: dict = {
    "session_id": "",
    "transcript_path": "",
    "cwd": "",
    "last_compile_turn": 0,
    "last_compile_time": "",
    "edits_since_compile": 0,
    "turns_since_compile": 0,
    "running_summary": "",
}


def load_state() -> dict:
    """Load session state from disk, returning defaults if missing/corrupt."""
    try:
        if STATE_FILE.exists():
            return {**DEFAULT_STATE, **json.loads(STATE_FILE.read_text(encoding="utf-8"))}
    except Exception:
        pass
    return dict(DEFAULT_STATE)


def save_state(state: dict) -> None:
    """Atomically write session state to disk."""
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def reset_state_for_session(session_id: str, transcript_path: str, cwd: str) -> dict:
    """Initialise a fresh state for a new session."""
    state = dict(DEFAULT_STATE)
    state["session_id"] = session_id
    state["transcript_path"] = transcript_path
    state["cwd"] = cwd
    state["last_compile_time"] = _now_iso()
    save_state(state)
    return state


def reset_compile_counters(state: dict, current_turn: int) -> dict:
    """Reset per-window counters after a successful mid-session compile."""
    state["last_compile_turn"] = current_turn
    state["last_compile_time"] = _now_iso()
    state["edits_since_compile"] = 0
    state["turns_since_compile"] = 0
    return state


# ── Lockfile ──────────────────────────────────────────────────────────────────

def acquire_lock() -> bool:
    """
    Try to acquire the compile lock.
    Returns True if the lock was acquired, False if another process holds it.
    Steals stale locks (older than LOCK_STALE_SECONDS).
    """
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()

    if LOCK_FILE.exists():
        try:
            data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            age = now - data.get("ts", 0)
            if age < LOCK_STALE_SECONDS:
                # Another process holds a fresh lock
                return False
            # Lock is stale — steal it
        except Exception:
            pass  # corrupt lock file, overwrite it

    _write_lock(now)
    return True


def release_lock() -> None:
    """Release the compile lock."""
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def is_locked() -> bool:
    """Return True if a non-stale lock is held."""
    if not LOCK_FILE.exists():
        return False
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        age = time.time() - data.get("ts", 0)
        return age < LOCK_STALE_SECONDS
    except Exception:
        return False


def _write_lock(ts: float) -> None:
    LOCK_FILE.write_text(
        json.dumps({"pid": os.getpid(), "ts": ts}),
        encoding="utf-8",
    )


# ── Running summary ───────────────────────────────────────────────────────────

def read_running_summary() -> str:
    """Return the summary written by the last mid-session compilation, or ''."""
    try:
        if SUMMARY_FILE.exists():
            return SUMMARY_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def clear_running_summary() -> None:
    try:
        SUMMARY_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def minutes_since(iso_str: str) -> float:
    """Return minutes elapsed since an ISO timestamp string, or inf if unparseable."""
    if not iso_str:
        return float("inf")
    try:
        then = datetime.fromisoformat(iso_str)
        now = datetime.now(tz=then.tzinfo or timezone.utc)
        return (now - then).total_seconds() / 60
    except Exception:
        return float("inf")


# ── Auto-update log ───────────────────────────────────────────────────────────

def log_auto_update(event: str, details: str = "") -> None:
    """Append one timestamped line to auto-updates.log. Best-effort, never raises."""
    try:
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} | {event:<20} | {details}\n" if details else f"{ts} | {event}\n"
        AUTO_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUTO_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
