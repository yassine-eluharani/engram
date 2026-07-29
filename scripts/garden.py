"""
KB gardening — compact one project's articles.

Background compiles only ever ADD to the KB; nothing merges near-duplicate
bullets or removes claims that stopped being true. Run this occasionally
(monthly is plenty) per project:

    uv run python scripts/garden.py <project-slug>

Runs a foreground `claude -p` so you can watch it work. Tools are scoped to
read/write plus `rm` (needed when merging a flat article into a topic directory).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

from shared import COMPILE_MODEL, find_claude  # noqa: E402

KNOWLEDGE_DIR = ROOT / "knowledge"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: uv run python scripts/garden.py <project-slug>")
        return 1

    slug = sys.argv[1]
    project_dir = KNOWLEDGE_DIR / "projects" / slug
    if not project_dir.exists():
        print(f"No KB folder at {project_dir}")
        return 1

    prompt = (
        f"KB gardening pass for project `{slug}` — do not ask questions, just do the work.\n\n"
        f"Articles: {project_dir}/\n"
        f"Index: {KNOWLEDGE_DIR}/index.md | Log: {KNOWLEDGE_DIR}/log.md\n\n"
        f"Read EVERY article in the project folder, then compact:\n"
        f"1. Merge near-duplicate bullets and repeated facts (keep the most recent/specific wording)\n"
        f"2. Delete claims contradicted by newer articles — the newest claim wins\n"
        f"3. Remove ephemeral task-state that no longer matters ('pending X' that clearly resolved)\n"
        f"4. Enforce granularity: flat articles >80 lines or 3+ H2 sections → split into a\n"
        f"   <topic>/ directory with _index.md + 20-50-line leaf files, then `rm` the old flat file\n"
        f"5. Fix broken [[wikilinks]] you encounter\n\n"
        f"Do NOT invent new content. Do NOT delete facts that are merely old — only duplicated,\n"
        f"contradicted, or resolved-ephemeral content.\n\n"
        f"Afterwards: update the project's rows in index.md (fresh summaries + today's date) and\n"
        f"append one line to log.md: `## <ISO timestamp> gardened | {slug} — <what changed>`.\n"
    )

    return subprocess.run(
        [
            find_claude(),
            "--allowedTools", "Read,Glob,Grep,Write,Edit,Bash(rm:*)",
            "--model", COMPILE_MODEL,
            "-p", prompt,
        ],
        cwd=str(ROOT),
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
