# devlab/runtime/memory — the filesystem dev-process memory store

**Status:** foundation laid 2026-06-16. Migration of existing records NOT yet run.
**Owner:** Hubert (dev-process / decisions / lab artifacts).
**Decision:** `decisions/cc.0.hubert.20260616.*.json` (genesis, dogfooded) and
`lab/design_docs/decisions/D-filesystem-memory-store-2026-06-16.md`.

---

## What this is

Every dev-process record — tickets, decisions, slates, chat, builder feedback,
judge verdicts, notes, design patterns, rules, per-project artifacts — is **one
pretty-printed JSON file** under a category folder here. The store is:

- **Grep-able.** Bodies are plain readable strings, never base64. Akien searches
  with `grep -r`. There is no query language and no index to keep in sync.
- **Git-backed.** The repo IS the backup and the history. `git log` over a file
  is that record's audit trail for free.
- **In the repo on purpose.** Per Akien: *"this violates nothing in the repo, but
  it backs up product development artifacts for free. I will change it later."*
  Location is provisional — it moves when `lab/` → `devlab/` completes. All writes
  route through ONE helper (`lab/claudecode/memory_emit.py`) so the move is a
  one-line change, not a sweep across thousands of files.

It is the design answer to "the palace system is not working": the Postgres palace
fragmented across four stores with no sync. Files + grep + git is the substrate
that matches how CC actually operates ([[feedback_design_for_cc_mental_model]]).

---

## Categories (folders)

```
architecture        symbol maps, whys, the substrate the codebase-inference rack
                    device hangs off of; whys link down to commits→tickets→decisions
artifacts           build outputs, generated docs, captured fixtures
boot                boot-time state that must load before Postgres is up
chat.cc.0           CC.0 conversation emissions (literal dotted folder name)
chat.igor           Igor conversation emissions (literal dotted folder name)
slates              daily slates
sessions            session summaries (/savestate output)
decisions           decision records (/sorted output)
builder_feedback    feedback from/about builders
judge               judge/critic verdicts (name may be retuned to match the agent)
notes              free-form notes
design_patterns     reusable design patterns
projects/acurite    per-project: Acurite
projects/uu         per-project: UnseenUniversity
projects/swadl      per-project: SWADL
rules               design rules / constraints
tickets             ticket records
proofs              proof-on-close artifacts (proof_emitter.py output) — a thing's
                    intention, the authenticated red->green evidence, bound to a
                    commit via links.commits. A ticket closes by pointing at one.
```

Reserved names (`judge`, `chat.cc.0`, `chat.igor`) are kept exactly as Akien named
them. Renames are his call.

---

## Filename convention

```
<emitter>[.<ns>...].<yyyymmdd>.<hhmmssuuuuuu>.json
```

- `emitter` — who emitted it: `cc.0`, `igor`, `hubert`, `haiku`, … May contain dots.
- `<ns>` — zero or more optional lower-order namespace segments.
- `yyyymmdd` — date, 8 digits.
- `hhmmssuuuuuu` — time to microseconds, 12 digits (6 for seconds-field, 6 µs).

Example: `cc.0.hubert.20260616.143022501234.json`
→ emitter+ns = `["cc", "0", "hubert"]`, stamp = `20260616.143022501234`.

### PARSE RULE (authoritative)

The **two dot-segments immediately before `.json`** are ALWAYS the date (8 digits)
and the time (12 digits). **Everything before them** is the dotted emitter + optional
namespaces. This is what lets a dotted emitter like `cc.0` round-trip unambiguously —
you split on `.`, peel the last two segments as the stamp, and the rest is the
emitter/namespace path. See `memory_emit.parse_filename()` for the reference impl.

---

## Record envelope

```json
{
  "id": "<the filename stem, minus .json>",
  "emitter": "cc.0",
  "namespace": ["hubert"],
  "category": "decisions",
  "kind": "decision",
  "emitted_at": "2026-06-16T14:30:22.501234",
  "links": { "goals": [], "decisions": [], "tickets": [], "commits": [], "whys": [] },
  "body": { "...": "the record payload — plain readable strings" }
}
```

- `id` equals the filename stem — self-identifying if a file is ever moved.
- `links` use **semantic ids** (`D-…`, `T-…`, commit sha, goal id, why id), NOT
  emission filenames. A record points at *what it is about*, not at *which file*.
  This is the spine of "build from intent": goals → decisions → tickets → commits,
  and whys ← back up the same chain.
- `kind` is the semantic record type within a category (`decision`, `ticket`,
  `slate`, `chat`, `verdict`, `why`, `symbol`, …).

---

## How to write

Always through the one chokepoint — never hand-write a file:

```bash
python3 lab/claudecode/memory_emit.py \
  --category decisions --emitter cc.0 --namespace hubert --kind decision \
  --body-file /tmp/record.json \
  --links '{"tickets":["T-build-log-digester"],"decisions":["D-rewind-as-workflow-primitive-2026-06-16"]}'
```

Or from Python: `from lab.claudecode.memory_emit import emit`.

---

## Migration policy (projection, not relocation)

When existing records move in:

1. **Additive.** Sources (Postgres palace, `lab/design_docs/`, slates,
   `decisions_log.dsb`, `cc_queue.py` DB) stay authoritative until a *separate,
   later* cutover phase. This pass only projects copies in. Nothing is deleted.
2. **Idempotent.** Stamp each emission with the record's **ORIGINAL** timestamp via
   `--stamp` (not migration-time). Same record → same stamp → same filename → a
   re-run atomically overwrites in place. Re-running the whole migration never
   duplicates.
3. **Day-only sources** (slates, `decisions_log.dsb` rows with no clock time) MUST
   derive their stamp via `memory_emit.stamp_for_day_only(record_id, date)` — never
   an ad-hoc per-migrator scheme. It hashes the record's **semantic id** into a valid
   clock time, so every migrator (including parallel Haiku workers) derives the
   identical filename for the identical record. This is the one part of the
   convention where a mismatch is NOT a cheap re-run — divergent schemes produce
   silent near-duplicates — so it is locked in code, not prose.

Newer records are easier to match (goals→decisions→tickets→commits links are fresh);
older ones are harder. Haiku agents do the bulk matching; the links get richer as we
go.

---

## Two layers of intention (forward direction)

The `links` spine is built to carry **two intention layers**:

- **User intention** — what Akien asked for (captured in decision/ticket bodies).
- **Architectural intention** — the system-embodied constraints (already partially
  built: intent-extractor, `constraint_normalizer.py`).

Later tooling blends the two over the `links` graph → *build from intent*. The
`architecture/` folder becomes the substrate the codebase-inference rack device
hangs symbol maps and whys off of, every why linking back down to a commit, a
ticket, a decision.
