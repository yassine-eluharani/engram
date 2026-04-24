# Knowledge Base

You maintain a persistent global knowledge base at `~/.claude/memory-compiler/knowledge/`.
At session start, you receive injected context showing the current project and its KB articles.

## Structure

```
~/.claude/memory-compiler/knowledge/    ← Obsidian vault root (point Obsidian here)
├── index.md                            # Global catalog — one row per article/directory
├── log.md                              # Append-only operation log
├── concepts/                           # Global, cross-project knowledge
│   └── <concept-name>.md              # One file per concept
├── projects/
│   └── <project-slug>/                # One folder per project
│       ├── overview.md                 # Always create this first (flat)
│       ├── <small-topic>.md            # Flat file: under ~60 lines, single topic
│       └── <large-topic>/             # Topic directory: >80 lines OR 3+ H2 sections
│           ├── _index.md              #   2-3 sentence overview + list of leaves
│           ├── <subtopic-a>.md        #   20-50 lines, self-contained leaf
│           └── <subtopic-b>.md        #   20-50 lines, self-contained leaf
└── qa/
    └── <question-slug>.md             # One file per filed Q&A
```

**Granularity rules:**
- **Keep flat** when an article is under ~60 lines and covers one topic
- **Split into a directory** when an article grows past ~80 lines OR has 3+ distinct H2 sections
- After splitting: create `<topic>/_index.md` (overview + leaf list) + individual leaf files; delete the old flat file
- **Leaf files** (20-50 lines): self-contained, wikilink to siblings and to `_index`

Wikilinks use Obsidian format: `[[projects/my-app/auth/_index]]` (no .md extension).

## When to update the KB

Update proactively (without being asked) when:
- A significant architectural decision is made
- A non-obvious pattern or convention is established
- A tricky bug is solved that's worth remembering

Update when asked:
- "update the KB / save this / remember this"
- "create a project overview / document this pattern"

## How to update

1. **One article = one file** — never merge unrelated topics into one file
2. **Create/update the article** at the correct path (see granularity rules above)
3. **Update `knowledge/index.md`** — add or update the row:
   - Flat article: `| [[projects/<slug>/<name>]] | one-line summary | <slug> | YYYY-MM-DD |`
   - Topic directory: `| [[projects/<slug>/<topic>/_index]] | leaf1, leaf2, leaf3 | <slug> | YYYY-MM-DD |`
4. **Append to `knowledge/log.md`**:
   `## [ISO timestamp] updated | projects/<slug>/<name>.md — reason`
5. **Link between articles** — use `[[wikilinks]]` to connect related files

## Article format (Obsidian-compatible)

```markdown
---
title: "Descriptive Title"
project: "<slug>"           # or "global" for cross-project concepts
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# Descriptive Title

One paragraph explaining what this is and why it matters.

## Key Points

- Point one
- Point two

## Details

Deeper explanation. Multiple paragraphs fine.

## Related

- [[projects/<slug>/overview]] — link to related articles with a note
- [[concepts/some-concept]] — what connects them
```

## Reading articles during a session

At session start, you receive:
- The full global index (all articles listed with one-line summaries)
- A hierarchical project article listing: topic directories collapsed to one row showing leaf names and count; flat files listed individually
- Hot articles in full: if the project uses `_index.md` directories, the 2 most recently modified `_index.md` files plus each directory's most recent leaf; otherwise the 2 most recently modified flat articles

**Before responding to any substantive request**, scan the project article listing
and proactively read any articles that are likely relevant — don't wait to be asked.
Use the Read tool: `~/.claude/memory-compiler/knowledge/projects/<slug>/<name>.md`

**Follow wikilinks.** When you read an article and it links to another via `[[...]]`,
read the linked article too if it's relevant to what the user is asking. Stop when
the chain of linked articles is no longer adding useful context.

Example: user asks about auth → read `auth-patterns.md` → it links to `overview.md`
and `stripe-integration.md` → read those too if auth context from them is needed.

## New project behavior (comprehensive first-session scan)

When the injected context shows **no KB articles for the current project**, this is
the first time Claude has worked in this project. On your **first response**, before
doing anything else, do a thorough codebase scan and build out the full project KB.

**Do not ask for permission.** The user has set this system up to work this way.
Just say "New project detected — building KB..." and proceed.

### What counts as a "project"

Anything — not just code. Examples:
- A software codebase (any language)
- A research topic (papers, notes, ideas)
- A writing project (book, essays, docs)
- A business or product (strategy, decisions, plans)
- A personal domain (health, learning, goals)
- A client engagement (meetings, requirements, deliverables)

The KB adapts to whatever is in the folder.

### Scan order

1. **Understand what kind of project this is** — look at file types, folder names,
   README, any manifest files to determine the nature of the project
2. **Read the most informative top-level files** — README, briefs, specs, manifests,
   config files, or whatever gives the best high-level picture
3. **Go deeper into the key content** — source files, documents, notes, data files —
   enough to build a complete picture, not necessarily every single file
4. **Infer structure and conventions** — how is the work organized, what patterns
   or decisions are evident

### Articles to create

Do NOT use a fixed template. Decide which articles to create based on what's
actually in the project. Every project gets an `overview.md`. Beyond that,
create whatever articles best capture the full picture.

**Examples by project type** (not exhaustive — use judgment):

| Project type | Possible articles |
|---|---|
| Code | overview, architecture, patterns, dependencies, api, database, config, testing |
| Research | overview, thesis, sources, open-questions, key-concepts, timeline |
| Writing | overview, outline, characters/entities, themes, style-guide, status |
| Business | overview, goals, decisions, stakeholders, roadmap, constraints |
| Personal | overview, goals, context, history, patterns, next-steps |

Create only what's relevant and meaningful. Each article should be thorough and
self-contained — written as if explaining this project to someone smart who has
never seen it before.

Link articles together with `[[wikilinks]]`. Every article should reference
`overview.md` and any directly related articles.

After creating all articles, update `knowledge/index.md` with all new entries,
then append a single entry to `knowledge/log.md`:
`## [timestamp] initial-scan | <slug> — N articles created`

### Subsequent sessions

The KB already exists. Do NOT re-scan. Just read articles as needed based on context.

## Daily log

`daily/YYYY-MM-DD.md` is auto-populated by the session-end hook with raw conversation turns.
When asked to "compile the daily log" or "summarize today's session":
1. Read today's daily log file
2. Extract key insights, decisions, patterns, bugs
3. Create or update the appropriate KB articles
4. Update the index
