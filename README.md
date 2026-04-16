# Engram

> *An engram is the physical trace a memory leaves in the brain. This is the digital equivalent — a persistent, compounding knowledge base that grows with every Claude Code session.*

**Engram** gives Claude Code a long-term memory that works across every project, every session, and every working directory — automatically.

No external API. No vector database. No embeddings. Just structured markdown files that Claude maintains, that you can open in Obsidian.

---

## The Problem

Claude Code is brilliant inside a session. But it forgets everything the moment you close it. Every new session starts from zero:

- You re-explain your project architecture
- You re-describe your patterns and conventions
- You rediscover the same bugs and their fixes
- You re-establish context that took 20 minutes to build last time

This gets worse as projects grow. The more you've worked with Claude, the more you're repeating yourself.

## The Solution

Engram builds a persistent wiki — a structured collection of Obsidian-compatible markdown files — that Claude maintains automatically. When you open Claude in any directory:

1. Engram detects which project you're in
2. Injects the relevant knowledge base articles into Claude's context
3. Claude knows your project without you having to explain it again

When you work in a project for the first time, Claude does a comprehensive scan and builds out the entire KB automatically. Every subsequent session, Claude picks up exactly where it left off.

The KB grows over time. Decisions, patterns, bugs, discoveries — all filed into the right place, cross-linked with wikilinks, and available in every future session.

---

## How It Works

```
Your conversation  ──▶  Claude reads & writes  ──▶  knowledge/projects/<project>/
                                                      ├── overview.md
                                                      ├── architecture.md
                                                      ├── patterns.md
                                                      └── ...
                                         ▲
                         SessionStart hook injects
                         relevant articles into context
```

### The three layers

**1. Raw source** — your codebase, documents, notes. Claude reads these but never modifies them.

**2. The wiki** — `knowledge/` — a directory of Claude-maintained markdown files. One file per concept. Obsidian wikilinks between them. Claude owns this layer entirely — creating, updating, and cross-referencing articles as you work.

**3. The schema** — `AGENTS.md` and your `~/.claude/CLAUDE.md` — tells Claude how the wiki is structured, what conventions to follow, and how to behave in new vs. existing projects.

### What gets stored

Engram is not just for code. It works for anything:

| Project type | What gets filed |
|---|---|
| Software | Architecture, patterns, API design, gotchas, decisions |
| Research | Thesis, sources, open questions, key concepts, timeline |
| Writing | Outline, characters/entities, themes, style guide, status |
| Business | Goals, decisions, stakeholders, roadmap, constraints |
| Personal | Goals, context, history, patterns, next steps |
| Anything | Whatever is meaningful and worth remembering |

### Token efficiency

Engram is designed to minimize token usage at session start:

- **Global index** — always injected (just a table of titles + summaries, lightweight)
- **Project article listing** — titles and first line only, not full content
- **2 most recently modified articles** — full content ("hot context")
- **Recent daily log tail** — last 20 lines

Total: ~3–5k tokens per session, regardless of how large the KB grows. Claude reads additional articles on demand via the Read tool when the content of the session requires it.

---

## Architecture

```
engram/
├── hooks/
│   ├── session-start.py     # Injects project-aware KB context at session start
│   └── session-end.py       # Saves conversation turns to daily log (no API)
├── scripts/
│   ├── config.py            # Path constants
│   ├── utils.py             # Shared helpers
│   └── lint.py              # KB health checks (structural, no LLM needed)
├── knowledge/               # The wiki — point Obsidian here as a vault
│   ├── index.md             # Master catalog — every article with one-line summary
│   ├── log.md               # Append-only operation log
│   ├── concepts/            # Global, cross-project knowledge articles
│   ├── projects/
│   │   └── <slug>/          # One folder per project
│   │       ├── overview.md
│   │       └── *.md         # One file per topic
│   └── qa/                  # Filed Q&A answers
├── daily/                   # Raw session logs — auto-populated by hooks
├── AGENTS.md                # KB schema and article format reference
├── CLAUDE.md                # Instructions for Claude (copied to ~/.claude/CLAUDE.md)
├── pyproject.toml           # Python dependencies (uv)
└── install.sh               # One-command installer
```

### Session lifecycle

```
Claude Code starts
       │
       ▼
SessionStart hook fires
  • Reads cwd from hook payload
  • Detects project slug (git remote → folder name)
  • Loads global index + project article listing + 2 hot articles
  • Injects as additionalContext (invisible to user, visible to Claude)
       │
       ▼
First message from user
  ├─ If NEW project (no KB articles):
  │    Claude does comprehensive codebase scan
  │    Creates 5–10 KB articles covering the full picture
  │    Updates global index
  │
  └─ If EXISTING project:
       Claude reads injected context
       Proactively reads relevant articles via Read tool
       Follows [[wikilinks]] to connected articles as needed
       │
       ▼
During session
  Claude updates KB articles when:
    • Significant decisions are made
    • Patterns or conventions are established
    • Non-obvious bugs are solved
    • User asks to "save / remember / update the KB"
       │
       ▼
Session ends
  SessionEnd hook fires
    • Extracts last 30 conversation turns from transcript
    • Appends to daily/YYYY-MM-DD.md (no API call)
```

### Knowledge base structure

Every article is a standalone Obsidian-compatible markdown file:

```markdown
---
title: "Auth Patterns"
project: "my-saas-app"
tags: [auth, supabase, rls]
created: 2026-04-16
updated: 2026-04-16
---

# Auth Patterns

One paragraph explaining the context.

## Key Points

- JWT tokens are verified at the edge via middleware
- RLS policies handle row-level access — never filter in application code
- Session refresh happens automatically via `supabase.auth.onAuthStateChange`

## Details

Deeper explanation with examples and gotchas...

## Related

- [[projects/my-saas-app/overview]] — project context
- [[projects/my-saas-app/database]] — RLS policy definitions
- [[concepts/supabase-rls]] — global RLS reference
```

Wikilinks use Obsidian format: `[[projects/my-saas-app/auth-patterns]]` (no `.md` extension). The entire `knowledge/` directory opens as an Obsidian vault with full graph view, backlinks, and search.

---

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| [Claude Code](https://claude.ai/code) | Latest | The CLI tool by Anthropic |
| [uv](https://docs.astral.sh/uv/) | Any | Fast Python package manager |
| Python | 3.12+ | Managed by uv |
| Obsidian | Any | Optional, for browsing the KB |

**No ANTHROPIC_API_KEY required.** Engram uses no external API calls. Claude Code itself is the LLM — it reads and writes the wiki files directly during sessions.

---

## Installation

### Quick install

```bash
git clone https://github.com/yourusername/engram.git
cd engram
./install.sh
```

Then restart Claude Code.

### What the installer does

1. Copies files to `~/.claude/engram/` (configurable with `--install-dir`)
2. Runs `uv sync` to install Python dependencies
3. Adds `SessionStart` and `SessionEnd` hooks to `~/.claude/settings.json`
4. Appends KB maintenance instructions to `~/.claude/CLAUDE.md`
5. Initializes the empty `knowledge/index.md` and `knowledge/log.md`

### Custom install directory

```bash
./install.sh --install-dir /path/to/your/preferred/location
```

### Manual installation

If you prefer to install manually or already have a `settings.json` you want to preserve:

**1. Copy the repo files:**

```bash
cp -r engram ~/.claude/engram
```

**2. Install dependencies:**

```bash
uv sync --directory ~/.claude/engram
```

**3. Add hooks to `~/.claude/settings.json`:**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --directory ~/.claude/engram python ~/.claude/engram/hooks/session-start.py",
            "timeout": 15
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --directory ~/.claude/engram python ~/.claude/engram/hooks/session-end.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

**4. Add instructions to `~/.claude/CLAUDE.md`:**

Copy the contents of `CLAUDE.md` (from this repo) and append them to your `~/.claude/CLAUDE.md`.

**5. Restart Claude Code.**

---

## Usage

### First session in a new project

Open Claude Code in any directory. On your first message, Claude will:

1. Detect it's a new project (no KB articles exist)
2. Do a comprehensive scan — reads the project structure, key files, entry points, config
3. Build out an extensive set of articles covering the full picture
4. Update the global index
5. Then respond to whatever you asked

This happens once per project. Every session after that, Claude loads from the existing KB.

### Day-to-day

Just use Claude Code normally. Engram works in the background:

- **Context is loaded automatically** — no commands needed
- **Articles are updated proactively** — Claude files important things without being asked
- **You can always ask explicitly:**
  - `"Update the KB with what we just decided"`
  - `"Save this pattern for next time"`
  - `"Create an article about our deployment setup"`
  - `"What do you know about our auth system?"` (Claude reads from the KB)
  - `"Compile today's session into the KB"` (Claude processes the daily log)

### Checking the KB

```bash
# Browse in Obsidian (point vault at this directory)
open ~/.claude/engram/knowledge/

# Run structural health checks (no API needed)
uv run --directory ~/.claude/engram python scripts/lint.py --structural-only

# View today's session log
cat ~/.claude/engram/daily/$(date +%Y-%m-%d).md

# See hook activity
tail -f ~/.claude/engram/scripts/session-end.log
```

### Working with Obsidian

1. Open Obsidian
2. `Open folder as vault` → select `~/.claude/engram/knowledge/`
3. Enable `Dataview` plugin for querying frontmatter
4. Use Graph View to see connections between articles

Every article has YAML frontmatter with `project`, `tags`, `created`, `updated` — fully compatible with Dataview queries and Obsidian search.

---

## How Claude maintains the KB

### Proactive updates (no prompting needed)

Claude updates the KB automatically when:
- A significant architectural decision is made
- A non-obvious pattern or convention is established
- A tricky bug is solved that's worth remembering
- New dependencies or tools are introduced

### On demand

Any time you say:
- `"Update the KB"` / `"Save this"` / `"Remember this for next time"`
- `"Create a project overview"`
- `"Document this pattern"`
- `"Compile today's session"`

### Reading during sessions

At session start, Claude receives the global index + project article listing + 2 hot articles. When the session requires more context, Claude proactively reads additional articles via the Read tool — following `[[wikilinks]]` to connected articles as needed. You never need to specify which files to read.

---

## Customization

### Adjust what loads at session start

Edit `hooks/session-start.py`:

```python
MAX_CONTEXT_CHARS = 18_000   # Total character budget for injected context
HOT_ARTICLES = 2             # How many recent articles to load in full
MAX_LOG_LINES = 20           # Lines of daily log to include
```

### Change the project detection logic

By default, Engram detects projects from:
1. Git remote URL (repo name)
2. Folder name (fallback)

To override, edit `detect_project()` in `hooks/session-start.py`.

### Add custom article types

Edit `AGENTS.md` to define new article formats and instruct Claude when to create them. The schema is the single source of truth for KB conventions.

### Change the KB location

By default, the KB lives at `~/.claude/engram/knowledge/`. To move it, update `ROOT` in `scripts/config.py` and re-install.

---

## KB health checks

Engram includes a structural linter that runs 6 checks with no API needed:

```bash
uv run --directory ~/.claude/engram python scripts/lint.py --structural-only
```

| Check | What it catches |
|---|---|
| Broken links | `[[wikilinks]]` pointing to non-existent articles |
| Orphan pages | Articles with zero inbound links |
| Orphan sources | Daily logs not yet processed |
| Stale articles | Source logs changed since last KB update |
| Missing backlinks | A links to B but B doesn't link back |
| Sparse articles | Articles under 200 words |

Reports are saved to `reports/lint-YYYY-MM-DD.md`.

---

## Privacy

- Everything stays local on your machine
- No data is sent to any external service
- No API key required
- The `daily/` and `knowledge/` directories are gitignored — they are never committed
- The repo you clone/fork only contains the system code, not your personal knowledge

---

## Inspiration

Engram is inspired by [Andrej Karpathy's LLM Knowledge Base](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern and Vannevar Bush's original [Memex](https://en.wikipedia.org/wiki/Memex) concept (1945) — a personal, curated knowledge store with associative trails between documents.

The key difference from RAG systems: **the wiki is compiled once and kept current**, not re-derived from scratch on every query. The LLM (Claude) does the bookkeeping — summarizing, cross-referencing, filing, and maintaining consistency. You just work.

> *"The human's job is to curate sources, direct the analysis, ask good questions, and think about what it all means. The LLM's job is everything else."*

---

## Contributing

Contributions welcome. Some directions worth exploring:

- **Windows support** — hooks and path handling for Windows users
- **Project aliases** — map multiple directory names to the same project
- **KB search CLI** — a lightweight search tool for querying the KB from the terminal
- **Obsidian plugin** — surface KB gaps and trigger Claude updates from within Obsidian
- **Export formats** — generate reports, slide decks, or docs from KB articles

Please open an issue before starting significant work.

---

## License

MIT
