# Shared Palace Schema

**Status:** Design (2026-05-07)
**Decision:** D-shared-palace-schema-2026-05-07
**Gates:** T-adc-summarizer-device, context-load redesign, docs tree migration

---

## Overview

The palace is a **shared agent-layer resource** serving four first-class consumers:
- **Akien** — reads rules, project context, capability inventory
- **CC** (Claude Code) — loads session context, checks rules, navigates decisions
- **Igor** — his palace stays in TheIgors postgres; this palace federates via pointer node
- **Rack-Minion** — reads capability map, project standards, task context

**Storage:** Postgres table in the agent_datacenter rack database (not TheIgors).
**Igor's palace stays separate** — `palace.projects.theigors` is a pointer node only.

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

Per-project context. One subtree per project.

### Standard subtree per project

Every project gets the same four nodes:

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

palace.projects.<name>.decisions
  title: <Project> — decision log index
  content: Pointer to decisions_log.dsb + last 5 D-ids with one-line summaries.
           Full decision files live in lab/design_docs/decisions/.
```

### Registered projects (initial)

| Project | Summary node | Notes |
|---|---|---|
| `agent_datacenter` | `palace.projects.agent_datacenter.summary` | Primary rack; owns this schema |
| `theigors` | `palace.projects.theigors.summary` | **Pointer only** — see federation below |

---

## Federation: Igor's palace

Igor's palace lives in TheIgors postgres. It is **not merged** into this database.

```
palace.projects.theigors
  title: TheIgors — federated palace pointer
  content: Igor's palace is at postgresql://igor:...@127.0.0.1/Igor-wild-0001,
           table memory_palace, root path "theigors/".
           Query via: psql -c "SELECT path, title FROM memory_palace WHERE path LIKE 'theigors/%' ORDER BY path"
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
    node_type   TEXT DEFAULT 'doc',         -- doc | pointer | index
    updated_at  TIMESTAMPTZ DEFAULT now(),
    metadata    JSONB DEFAULT '{}'
);
```

`metadata` fields used:
- `pointer_to` (string) — for `node_type='pointer'`, where the real content lives
- `gates` (array) — ticket IDs this node's existence unblocks
- `project` (string) — project name for `palace.projects.*` nodes

---

## Gating table

What each downstream ticket needs from this schema before it can start:

| Ticket | Needs from palace schema |
|---|---|
| T-adc-summarizer-device | `palace.shared.capabilities.devices` node shape — knows where to register |
| context-load redesign | `palace.shared.*` namespace populated — reads rules/akien/capabilities at session start |
| docs tree migration | `palace.projects.agent_datacenter.*` namespace — knows where docs land |
| T-cc-skills-triage | `palace.shared.capabilities.skills` node shape — knows where cross-machine skills register |

---

## Bootstrap sequence

1. Create `palace` table in rack Postgres (migration)
2. Seed `palace.shared.akien.*` from existing CC auto-memory + CLAUDE.md
3. Seed `palace.shared.rules.*` from TheIgors memory_palace `theigors/rules/*` (copy, not federate)
4. Seed `palace.projects.agent_datacenter.*` from CLAUDE.md + phase map
5. Add `palace.projects.theigors` pointer node
6. Update context-load to read `palace.shared.*` instead of flat CLAUDE.md scan

Each step is a separate ticket. This doc is step 0 (schema exists, no rows yet).
