# Shared Palace Schema

**Status:** Design (2026-05-07, revised 2026-05-07)
**Decision:** D-shared-palace-schema-2026-05-07
**Gates:** context-load redesign, docs tree migration

---

## Overview

The palace is a **shared agent-layer resource** serving four first-class consumers:
- **Akien** — reads rules, project context, capability inventory
- **CC** (Claude Code) — loads session context, checks rules, navigates decisions
- **Igor** — his palace stays in TheIgors postgres; this palace federates via pointer node
- **Rack-Minion** — reads capability map, project standards, task context

**Storage:** Postgres table in the UnseenUniversity rack database (not TheIgors).
**Igor's palace stays separate** — `palace.projects.theigors` is a pointer node only.

**Organizing principle:** Flat namespaces with tags, not nested project hierarchies.
Projects are tags (`metadata.tags`) on nodes, not path prefixes. A ticket or decision
that spans ADC and Igor gets both tags — no silo walls, no awkward cross-referencing.

---

## Namespace: `palace.shared`

Cross-agent context that every consumer needs.

### `palace.shared.akien`

Who Akien is, how he works, what he cares about.

```
palace.shared.akien.profile
  title: Akien profile
  content: Role, background, communication preferences, working style.

palace.shared.akien.working_style
  title: Working style and preferences
  content: How Akien and agents collaborate — latitude compression, context-is-compute,
           preferred response style, when to ask vs proceed.
```

### `palace.shared.rules`

Coding and workflow rules that apply across all projects.

```
palace.shared.rules.coding
  title: Coding standards
  content: Language conventions, no-SQLite, no-TheIgors-imports in ADC, etc.

palace.shared.rules.commits
  title: Commit conventions
  content: Commit message format, stage-by-name, no --no-verify, etc.

palace.shared.rules.memory
  title: Memory rules
  content: What to save in CC auto-memory vs palace vs code comments.

palace.shared.rules.database
  title: Database rules
  content: Postgres-or-flat-file only, integration tests hit real Postgres, etc.

palace.shared.rules.budget
  title: Budget rules
  content: OR spend awareness, burn-rate thresholds, escalation triggers.

palace.shared.rules.collaboration
  title: Collaboration rules
  content: When to ask vs proceed, scope discipline, check-with-Akien triggers.

palace.shared.rules.safeguards
  title: Inertia / safeguards
  content: HIGH-inertia files requiring pre-approval, LOW-inertia defaults.
```

### `palace.shared.capabilities`

What's built and available across the rack.

```
palace.shared.capabilities.index
  title: Capability inventory index
  content: Pointer to per-device and per-project capability nodes.

palace.shared.capabilities.devices
  title: Installed devices and their APIs
  content: One entry per device: name, purpose, endpoint, key tools/routes.
           Supersedes per-session capability discovery.

palace.shared.capabilities.skills
  title: CC skills inventory
  content: List of available /skills, what each does, when to use it.
           Cross-machine skills only (Igor-internal skills stay in TheIgors palace).
```

### `palace.shared.audits`

Audit check registry — persistent checks that run at day-close.

```
palace.shared.audits.registry
  title: Registered audit checks
  content: Mirrors audit_runner.py registered checks. Pointer + description.
           Authoritative source remains audit_runner.py; this node is the human-readable index.
```

---

## Namespace: `palace.projects`

Per-project orientation nodes. Three nodes per project — summary, map, standards.
Tickets and decisions are **not** nested here; they live in flat namespaces with tags.

### Standard nodes per project

```
palace.projects.<name>.summary
  title: <Project> — executive summary
  content: What it is, current state, top 3 priorities right now.
           CC reads this first when loading project context.

palace.projects.<name>.map
  title: <Project> — architecture map
  content: Key components, how they connect, where the seams are.
           Enough to navigate the codebase without reading it.

palace.projects.<name>.standards
  title: <Project> — standards and conventions
  content: Project-specific rules beyond palace.shared.rules.
           E.g. ADC: BaseDevice/BaseShim required, no standalone functions doing device work.
```

No `.decisions` sub-node — decisions are flat at `palace.decisions.*` with tags.

### Registered projects (initial)

| Project | Summary node | Notes |
|---|---|---|
| `UnseenUniversity` | `palace.projects.unseen_university.summary` | Primary rack; owns this schema |
| `theigors` | `palace.projects.theigors.summary` | **Pointer only** — see federation below |

---

## Namespace: `palace.tickets`

All tickets, flat. Project/domain membership is via tags, not path.

```
palace.tickets.<T-id>
  title: <ticket title>
  content: Full ticket description (problem, affected files, scope, test plan).
  node_type: ticket
  metadata:
    tags: ["UnseenUniversity", "infrastructure"]   # project + domain tags
    status: pending | in_progress | done | blocked
    size: S | M | L | XL
    decision_id: D-xxx                              # which decision spawned this
    gate: T-yyy                                     # null or blocking ticket
    commit: abc1234                                 # set on close
    discussed_at: "2026-05-07T14:21:00Z"            # timestamp of key design conversation
    session: "20260507-2"                           # session that produced/closed this
```

---

## Namespace: `palace.decisions`

All decisions, flat. Tags carry project/domain association.

```
palace.decisions.<D-id>
  title: <one-line decision summary>
  content: |
    ## Decision
    <1-2 sentence summary>

    ## Alternatives considered
    <what was weighed>

    ## Constraints
    <what forced the choice>

    ## Rationale
    <why this over alternatives>

    ## Spawned tickets
    T-xxx, T-yyy

    ## Key commits
    abc1234
  node_type: decision
  metadata:
    tags: ["UnseenUniversity", "architecture"]
    date: "2026-05-07"
    status: open | closed
    spawned_tickets: ["T-xxx", "T-yyy"]
    session: "20260507-1"
```

---

## Namespace: `palace.sessions`

One node per CC session (between compactions). Written at savestate.

```
palace.sessions.<YYYYMMDD-N>
  title: Session YYYY-MM-DD #N — <theme>
  content: |
    In-flight at start: <T-ids or NONE>
    Decisions made: D-xxx (one line), D-yyy (one line)
    Tickets closed: T-aaa (commit abc), T-bbb (commit def)
    Tickets filed: T-ccc, T-ddd
    Notable choices: <CC+ moments, design pivots>
    In-flight at end: <T-ids or NONE>
  node_type: session
  metadata:
    tags: ["UnseenUniversity"]      # primary project for this session
    date: "2026-05-07"
    session_number: 2
    transcript: "20260507-2"        # pointer to palace.transcripts.<key>
```

---

## Namespace: `palace.transcripts`

Stripped session transcripts — human/assistant text only, no tool calls.
The drill-down layer: agents read `palace.sessions.*` first, land here when they need WHY.

```
palace.transcripts.<YYYYMMDD-N>
  title: Transcript YYYY-MM-DD #N
  content: <session JSONL filtered to role=user and role=assistant text content only>
  node_type: transcript
  metadata:
    tags: []
    session: "20260507-2"
    produced_by: "session_capture.py"
```

---

## Namespace: `palace.days`

Day-level roll-up. Written at day-close. The 10-day lookback layer for context-load.

```
palace.days.<YYYYMMDD>
  title: Day YYYY-MM-DD — <theme>
  content: |
    Sessions: N
    Decisions: D-xxx (one line), ...
    Tickets closed: T-aaa, T-bbb, ...
    Tickets filed: T-ccc, ...
    CC+ moments: <notable choices or insights>
    Next: <top priority>
  node_type: day_summary
  metadata:
    tags: []
    date: "2026-05-07"
    sessions: ["20260507-1", "20260507-2"]
```

---

## Federation: Igor's palace

Igor's palace lives in TheIgors postgres. It is **not merged** into this database.

```
palace.projects.theigors
  title: TheIgors — federated palace pointer
  content: Igor's palace is at postgresql://unseen_university:...@127.0.0.1/unseen_university,
           table memory_palace, root path "unseenuniversity/".
           Query via: psql -c "SELECT path, title FROM memory_palace WHERE path LIKE 'unseenuniversity/%' ORDER BY path"
           Or via MCP tools when Igor is running: mcp__igor__memory_get(path=...)
```

The federation node is read-only from this palace's perspective — Igor's palace is Igor's palace.

---

## Node shape (Postgres row)

```sql
CREATE TABLE palace (
    path        TEXT PRIMARY KEY,           -- e.g. 'palace.shared.rules.coding'
    title       TEXT NOT NULL,              -- one-line human label
    content     TEXT,                       -- markdown body
    node_type   TEXT DEFAULT 'doc',         -- doc | pointer | ticket | decision | session | transcript | day_summary
    updated_at  TIMESTAMPTZ DEFAULT now(),
    metadata    JSONB DEFAULT '{}'
);

CREATE INDEX palace_tags_idx ON palace USING GIN (metadata jsonb_path_ops);
CREATE INDEX palace_node_type_idx ON palace (node_type);
CREATE INDEX palace_updated_idx ON palace (updated_at DESC);
```

`metadata` fields used:
- `tags` (array) — project/domain membership; replaces path hierarchy for association
  - query: `WHERE metadata @> '{"tags": ["UnseenUniversity"]}'`
- `pointer_to` (string) — for `node_type='pointer'`, where the real content lives
- `status` (string) — for tickets/decisions: pending | in_progress | done | blocked | open | closed
- `decision_id` (string) — on ticket nodes: which decision spawned this
- `session` (string) — on session/transcript/day nodes: `YYYYMMDD-N`
- `discussed_at` (ISO timestamp) — on ticket/decision nodes: key conversation timestamp

---

## Gating table

What each downstream ticket needs from this schema before it can start:

| Ticket | Needs from palace schema |
|---|---|
| context-load redesign | `palace.shared.*` + `palace.days.*` populated — reads rules/akien/capabilities + last 10 day summaries |
| docs tree migration | `palace.projects.unseen_university.*` nodes — knows where project docs land |
| session record writer | `palace.sessions.*` + `palace.transcripts.*` namespaces defined (this doc) |
| decision enrichment | `palace.decisions.*` namespace defined (this doc) — richer D-id nodes |

---

## Bootstrap sequence

1. **Create `palace` table + indexes** in rack Postgres (one migration script)
2. **Seed `palace.shared.akien.*`** from CC auto-memory + CLAUDE.md
3. **Seed `palace.shared.rules.*`** from TheIgors memory_palace `unseenuniversity/rules/*` (copy, not federate)
4. **Seed `palace.projects.unseen_university.*`** (summary, map, standards) from CLAUDE.md + phase map
5. **Add `palace.projects.theigors` pointer node** — federation pointer only
6. **Seed `palace.decisions.*`** from existing `lab/design_docs/decisions/D-*.md` stubs (enriched)
7. **Update context-load** to read `palace.shared.*` + `palace.days.*` (last 10) at session start
8. **Wire savestate** to write `palace.sessions.*` + `palace.transcripts.*` on each session close
9. **Wire day-close** to write `palace.days.*` roll-up on each day close

Each step is a separate ticket. This doc is step 0 (schema defined, no rows yet).

## Querying patterns

```sql
-- What's hot right now (context-load)
SELECT path, title, metadata->>'status' FROM palace
WHERE node_type IN ('ticket', 'decision')
  AND metadata->>'status' IN ('in_progress', 'pending')
ORDER BY updated_at DESC LIMIT 20;

-- Last 10 day summaries (context-load lookback)
SELECT path, title, content FROM palace
WHERE node_type = 'day_summary'
ORDER BY updated_at DESC LIMIT 10;

-- Find decisions by topic (fuzzy search)
SELECT path, title FROM palace
WHERE node_type = 'decision'
  AND (title ILIKE '%palace%' OR content ILIKE '%palace%')
ORDER BY updated_at DESC;

-- All tickets for a project (tag filter)
SELECT path, title, metadata->>'status' FROM palace
WHERE node_type = 'ticket'
  AND metadata @> '{"tags": ["UnseenUniversity"]}'
ORDER BY updated_at DESC;

-- Drill from session to transcript
SELECT t.content FROM palace s
JOIN palace t ON t.path = 'palace.transcripts.' || (s.metadata->>'transcript')
WHERE s.path = 'palace.sessions.20260507-2';
```
