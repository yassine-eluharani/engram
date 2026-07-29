---
description: Compact a project's Engram KB — merge duplicates, drop stale claims, fix broken links
argument-hint: [project-slug]
---

Run the Engram KB gardening pass.

Determine the slug: use `$ARGUMENTS` if provided; otherwise use the current project's slug shown in the injected Memory System context header.

Then run with Bash (set timeout to 600000 — it spawns a headless claude session that can take a few minutes):

```
uv run --directory __INSTALL_DIR__ python __INSTALL_DIR__/scripts/garden.py <slug>
```

When it finishes, read the last few lines of `__INSTALL_DIR__/knowledge/log.md` to find the `gardened` entry and report to the user what was compacted.
