"""
SessionStart hook - project-aware knowledge base context injection.

Loading strategy (token-efficient, scales to hundreds of articles):
  1. Global index.md        — always, full (it's just a summary table)
  2. Project article list   — filenames + first content line only (NOT full content)
  3. 2-3 most recently modified project articles — full content ("hot context")
  4. Last 20 lines of today's daily log

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

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
PROJECTS_DIR = KNOWLEDGE_DIR / "projects"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
DAILY_DIR = ROOT / "daily"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"

# Token budget
MAX_CONTEXT_CHARS = 18_000
HOT_ARTICLES = 2        # How many recently-modified articles to load in full
MAX_LOG_LINES = 20


def detect_project(cwd: str) -> str:
    """Detect project slug from git remote name, falling back to folder name."""
    if not cwd:
        return "global"

    # Try git remote
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

    # ── 1. Header (always) ─────────────────────────────────────────────
    header = (
        f"## Memory System\n"
        f"**Project:** `{slug}` | **Date:** {today.strftime('%A, %B %d, %Y')}\n"
        f"**KB root:** `~/.claude/memory-compiler/knowledge/`\n"
        f"Use the Read tool to load any article listed below in full."
    )
    parts.append(header)
    budget -= len(header)

    # ── 2. Global index (always — it's just a table) ───────────────────
    if INDEX_FILE.exists():
        index_text = INDEX_FILE.read_text(encoding="utf-8")
        entry = f"## Global Knowledge Index\n\n{index_text}"
    else:
        entry = "## Global Knowledge Index\n\n| Article | Summary | Project | Updated |\n|---------|---------|---------|---------|"

    if len(entry) <= budget:
        parts.append(entry)
        budget -= len(entry)

    # ── 3. Project article listing + hot articles ──────────────────────
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
        # Article listing (lightweight — title + one-line summary only)
        listing_lines = [f"## Project: `{slug}` — {len(articles)} article(s)\n"]
        listing_lines.append("| Article | Summary |")
        listing_lines.append("|---------|---------|")
        for path, summary in articles:
            rel = str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/").replace(".md", "")
            listing_lines.append(f"| [[{rel}]] | {summary} |")
        listing = "\n".join(listing_lines)

        if len(listing) <= budget:
            parts.append(listing)
            budget -= len(listing)

        # Hot articles: load the N most recently modified in full
        loaded = 0
        hot_parts = []
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

        if hot_parts:
            parts.append(
                f"## Recently Active Articles (full content)\n\n"
                + "\n\n---\n\n".join(hot_parts)
            )

    # ── 4. Recent daily log (tail only) ───────────────────────────────
    log_text = get_recent_log()
    log_entry = f"## Recent Daily Log\n\n{log_text}"
    if len(log_entry) <= budget:
        parts.append(log_entry)

    return "\n\n---\n\n".join(parts)


def main():
    cwd = ""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            payload = json.loads(raw)
            cwd = payload.get("cwd", "") or payload.get("working_directory", "")
    except Exception:
        pass

    if not cwd:
        cwd = os.getcwd()

    context = build_context(cwd)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    main()
