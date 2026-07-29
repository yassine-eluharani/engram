"""
Shared helpers for Engram hooks.

- session-state-<id>.json: tracks activity within one session (keyed by session_id
  so concurrent sessions don't clobber each other)
- compile.lock: prevents concurrent background compilations
- detect_project/slugify: single source of truth for project slugs — the
  SessionStart injector and the background compilers must agree, or the KB
  splits into parallel folders (Malath vs malath) on case-sensitive filesystems
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

IS_WINDOWS = os.name == "nt"

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
LOCK_FILE = SCRIPTS_DIR / "compile.lock"

# How old a lockfile must be (seconds) before we consider the process dead
LOCK_STALE_SECONDS = 180  # 3 minutes

# Single, append-only feed of automatic-update activity. Easy to tail.
AUTO_LOG_FILE = ROOT / "auto-updates.log"

# Background compiler settings — one place, used by stop.py, session-end.py, garden.py
COMPILE_MODEL = os.environ.get("ENGRAM_COMPILE_MODEL", "claude-sonnet-4-6")
COMPILE_ALLOWED_TOOLS = "Read,Glob,Grep,Write,Edit"

# Per-session files older than this get cleaned up at session start
SESSION_FILE_MAX_AGE_DAYS = 7


# ── Cross-platform process spawning ──────────────────────────────────────────

def find_claude() -> str:
    """Resolve the claude CLI. On Windows it's claude.cmd/claude.exe, which a
    bare Popen(["claude", ...]) can't find — which() returns the full path."""
    return shutil.which("claude") or "claude"


def spawn_detached(cmd: list[str], *, env=None, cwd=None, stdout=None, stderr=None) -> None:
    """Fire-and-forget a background process that survives the hook's exit.
    POSIX detaches via setsid; Windows needs creationflags instead."""
    kwargs: dict = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, env=env, cwd=cwd, stdout=stdout, stderr=stderr, **kwargs)


# ── Project detection ─────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-") or "global"


def detect_project(cwd: str) -> str:
    """Detect project slug from git remote name, falling back to folder name."""
    if not cwd:
        return "global"

    try:
        result = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            remote = result.stdout.strip()
            match = re.search(r"/([^/]+?)(?:\.git)?$", remote)
            if match:
                return slugify(match.group(1))
    except Exception:
        pass

    name = Path(cwd).name
    return slugify(name) if name and name not in ("", ".", "/") else "global"


# ── Session state (keyed by session_id) ───────────────────────────────────────

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


def _session_suffix(session_id: str) -> str:
    return re.sub(r"[^\w-]", "", session_id)[:8]


def state_file(session_id: str) -> Path:
    sid = _session_suffix(session_id)
    name = f"session-state-{sid}.json" if sid else "session-state.json"
    return SCRIPTS_DIR / name


def summary_file(session_id: str) -> Path:
    sid = _session_suffix(session_id)
    name = f"last-compile-summary-{sid}.txt" if sid else "last-compile-summary.txt"
    return SCRIPTS_DIR / name


def load_state(session_id: str = "") -> dict:
    """Load session state from disk, returning defaults if missing/corrupt."""
    try:
        f = state_file(session_id)
        if f.exists():
            return {**DEFAULT_STATE, **json.loads(f.read_text(encoding="utf-8"))}
    except Exception:
        pass
    return dict(DEFAULT_STATE)


def save_state(state: dict) -> None:
    """Atomically write session state to disk, keyed by state['session_id']."""
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    f = state_file(state.get("session_id", ""))
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(f)


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


def cleanup_old_session_files() -> None:
    """Delete per-session state/summary files older than SESSION_FILE_MAX_AGE_DAYS."""
    cutoff = time.time() - SESSION_FILE_MAX_AGE_DAYS * 86400
    for pattern in ("session-state-*.json", "last-compile-summary-*.txt"):
        for f in SCRIPTS_DIR.glob(pattern):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass


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

def read_running_summary(session_id: str = "") -> str:
    """Return the summary written by the last mid-session compilation, or ''."""
    try:
        f = summary_file(session_id)
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def clear_running_summary(session_id: str = "") -> None:
    try:
        summary_file(session_id).unlink(missing_ok=True)
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
