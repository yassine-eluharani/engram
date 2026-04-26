"""
SessionStart hook - project-aware knowledge base context injection.

Loading strategy (token-efficient, scales to hundreds of articles):
  1. Global index.md        — always, full (it's just a summary table)
  2. Project article list   — hierarchical: topic directories collapsed to one row,
                              flat files listed individually
  3. Hot articles           — if project uses _index.md directories: load 2 most
                              recently modified _index.md + their most recent leaf;
                              otherwise: 2 most recently modified flat articles
  4. Last 20 lines of today's daily log

Also initialises session-state.json with session_id, transcript_path, and cwd
for use by the Stop hook's mid-session compilation trigger.

Claude reads additional articles on demand via the Read tool during the session.
The index tells Claude what exists; it fetches what it needs.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Recursion guard — when a mid-session/session-end compile spawns a child `claude -p`,
# its SessionStart hook would otherwise clobber session-state.json with the wrong
# session_id/cwd, wiping the parent session's edit/turn counters.
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import (
    LOCK_FILE,
    LOCK_STALE_SECONDS,
    clear_running_summary,
    log_auto_update,
    release_lock,
    reset_state_for_session,
)

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
PROJECTS_DIR = KNOWLEDGE_DIR / "projects"
DAILY_DIR = ROOT / "daily"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"

# Token budget
MAX_CONTEXT_CHARS = 18_000
HOT_ARTICLES = 2
MAX_LOG_LINES = 20


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


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-") or "global"


def get_first_content_line(path: Path) -> str:
    """Return first non-empty, non-frontmatter, non-heading line from a markdown file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        in_frontmatter = False
        for line in lines:
            stripped = line.strip()
            if stripped == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            if stripped.startswith("#") or not stripped:
                continue
            return stripped[:120]
    except Exception:
        pass
    return ""


def list_project_articles(slug: str) -> list[tuple[Path, str]]:
    """
    List all markdown files under knowledge/projects/<slug>/ recursively.
    Returns list of (path, one-line-summary) sorted by modification time (newest first).
    """
    project_dir = PROJECTS_DIR / slug
    if not project_dir.exists():
        return []

    files = sorted(
        project_dir.rglob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    return [(f, get_first_content_line(f)) for f in files]


def build_project_listing(slug: str, articles: list[tuple[Path, str]]) -> str:
    """
    Build article listing. Topic directories are collapsed to one row showing
    leaf file names and count. Root-level flat files appear individually.
    """
    project_dir = PROJECTS_DIR / slug
    lines = [f"## Project: `{slug}` — {len(articles)} article(s)\n"]
    lines.append("| Article | Summary |")
    lines.append("|---------|---------|")

    seen_dirs: set[str] = set()
    for path, summary in articles:
        rel_parts = path.relative_to(project_dir).parts
        if len(rel_parts) == 1:
            # Root-level file
            rel = str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/").replace(".md", "")
            lines.append(f"| [[{rel}]] | {summary} |")
        else:
            # Subdirectory file — collapse to one row per directory
            dir_name = rel_parts[0]
            if dir_name in seen_dirs:
                continue
            seen_dirs.add(dir_name)
            dir_path = project_dir / dir_name
            leaves = sorted(
                dir_path.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            leaf_names = [p.stem for p in leaves if p.name != "_index.md"]
            leaf_summary = ", ".join(leaf_names[:6])
            if len(leaf_names) > 6:
                leaf_summary += f", +{len(leaf_names) - 6} more"
            index_rel = f"projects/{slug}/{dir_name}/_index"
            lines.append(f"| [[{index_rel}]] | {leaf_summary} ({len(leaf_names)} leaves) |")

    return "\n".join(lines)


def get_hot_articles(
    slug: str,
    articles: list[tuple[Path, str]],
    budget: int,
) -> tuple[list[str], int]:
    """
    Return (hot_chunks, remaining_budget).

    If the project uses _index.md topic directories: load the 2 most recently
    modified _index.md files plus each directory's most recently modified leaf.
    Otherwise fall back to loading the 2 most recently modified flat articles.
    """
    hot_parts: list[str] = []

    index_files = [(p, s) for p, s in articles if p.name == "_index.md"]

    if index_files:
        for index_path, _ in index_files[:HOT_ARTICLES]:
            # Load _index.md
            content = index_path.read_text(encoding="utf-8")
            rel = str(index_path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")
            chunk = f"### {rel}\n\n{content}"
            if len(chunk) <= budget:
                hot_parts.append(chunk)
                budget -= len(chunk)

            # Load most recently modified leaf in the same directory
            dir_leaves = [
                p for p, _ in articles
                if p.parent == index_path.parent and p.name != "_index.md"
            ]
            if dir_leaves:
                leaf = dir_leaves[0]  # already sorted newest-first
                content = leaf.read_text(encoding="utf-8")
                rel = str(leaf.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")
                chunk = f"### {rel}\n\n{content}"
                if len(chunk) <= budget:
                    hot_parts.append(chunk)
                    budget -= len(chunk)
    else:
        # Flat project — load N most recently modified articles
        loaded = 0
        for path, _ in articles:
            if loaded >= HOT_ARTICLES:
                break
            content = path.read_text(encoding="utf-8")
            rel = str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")
            chunk = f"### {rel}\n\n{content}"
            if len(chunk) <= budget:
                hot_parts.append(chunk)
                budget -= len(chunk)
                loaded += 1

    return hot_parts, budget


def get_recent_log() -> str:
    today = datetime.now(timezone.utc).astimezone()
    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = DAILY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            recent = lines[-MAX_LOG_LINES:] if len(lines) > MAX_LOG_LINES else lines
            return "\n".join(recent)
    return "(no recent sessions logged)"


def build_context(cwd: str) -> str:
    slug = detect_project(cwd)
    today = datetime.now(timezone.utc).astimezone()
    budget = MAX_CONTEXT_CHARS
    parts = []

    # ── 1. Header ─────────────────────────────────────────────────────────
    header = (
        f"## Memory System\n"
        f"**Project:** `{slug}` | **Date:** {today.strftime('%A, %B %d, %Y')}\n"
        f"**KB root:** `~/.claude/memory-compiler/knowledge/`\n"
        f"Use the Read tool to load any article listed below in full."
    )
    parts.append(header)
    budget -= len(header)

    # ── 2. Global index ────────────────────────────────────────────────────
    if INDEX_FILE.exists():
        index_text = INDEX_FILE.read_text(encoding="utf-8")
        entry = f"## Global Knowledge Index\n\n{index_text}"
    else:
        entry = (
            "## Global Knowledge Index\n\n"
            "| Article | Summary | Project | Updated |\n"
            "|---------|---------|---------|---------|"
        )

    if len(entry) <= budget:
        parts.append(entry)
        budget -= len(entry)

    # ── 3. Project article listing + hot articles ──────────────────────────
    articles = list_project_articles(slug)

    if not articles:
        note = (
            f"## ⚠ NEW PROJECT: `{slug}`\n\n"
            f"**No KB articles exist for this project yet.**\n"
            f"Per your CLAUDE.md instructions: on your first response, do a comprehensive\n"
            f"codebase scan and build out the full project KB before doing anything else.\n"
            f"Articles go in: `~/.claude/memory-compiler/knowledge/projects/{slug}/`"
        )
        if len(note) <= budget:
            parts.append(note)
            budget -= len(note)
    else:
        listing = build_project_listing(slug, articles)
        if len(listing) <= budget:
            parts.append(listing)
            budget -= len(listing)

        hot_parts, budget = get_hot_articles(slug, articles, budget)
        if hot_parts:
            parts.append(
                "## Recently Active Articles (full content)\n\n"
                + "\n\n---\n\n".join(hot_parts)
            )

    # ── 4. Recent daily log (tail only) ───────────────────────────────────
    log_text = get_recent_log()
    log_entry = f"## Recent Daily Log\n\n{log_text}"
    if len(log_entry) <= budget:
        parts.append(log_entry)

    return "\n\n---\n\n".join(parts)


def main():
    cwd = ""
    session_id = ""
    transcript_path = ""

    try:
        raw = sys.stdin.read()
        if raw.strip():
            payload = json.loads(raw)
            cwd = payload.get("cwd", "") or payload.get("working_directory", "")
            session_id = payload.get("session_id", "")
            transcript_path = payload.get("transcript_path", "")
    except Exception:
        pass

    if not cwd:
        cwd = os.getcwd()

    # Initialise session state for the Stop hook's mid-session trigger
    if session_id or transcript_path:
        reset_state_for_session(session_id, transcript_path, cwd)
        clear_running_summary()

    # Best-effort: clear a stale lock left behind by a crashed background compile
    try:
        import json as _json
        import time as _time
        if LOCK_FILE.exists():
            data = _json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            age = _time.time() - data.get("ts", 0)
            if age >= LOCK_STALE_SECONDS:
                release_lock()
                log_auto_update("STALE-LOCK-CLEARED", f"age={age:.0f}s pid={data.get('pid')}")
    except Exception:
        pass

    log_auto_update("SESSION-START", f"project={Path(cwd).name or 'unknown'} session={session_id[:8] if session_id else '?'}")

    context = build_context(cwd)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    main()
