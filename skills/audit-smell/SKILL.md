---
name: audit-smell
description: Post-code pre-test code-quality audit. Catches Sonnet's known failure modes (fix-one-leave-many, deprecated-path fallbacks, helpful-refactor diff drift) plus broader smells (bare except, silent-return-False, base-class-inheritance miss, dead code, doc rot, speculative flags, mocked DB, SQLite fallbacks). Returns PASS / AMEND. Runs on the staged diff or the post-edit working tree. Gated by diff-size threshold and ticket audit-emphasis tags.
model: sonnet
---

# audit-smell — Post-code, pre-test code quality

Fires after edits land but before tests run. The cheapest place in the
sprint flow to catch the wide class of "code wrote what it asked for but
not what it should have." This is the layer that catches Sonnet's known
failure modes — the rest is general code hygiene.

`audit-smell` is **judgment-heavy** by design — it reads diffs the way a
careful human reviewer would, with rule context. Haiku misses too many
of these in practice. Default model: Sonnet.

---

## Inputs

- **Diff under review**: the staged diff (`git diff --cached`) when run
  inside `/sprint`, or the working-tree diff (`git diff`) when run
  ad-hoc.
- **Ticket context**: the ticket's `description.Affected files` list and
  `tags`. Drives the diff-drift check and the audit-emphasis routing.
- **Palace context**:
  - `unseenuniversity/rules/inherit-base-class` — base-class inheritance rule
  - `unseenuniversity/rules/preferred_paths` — deprecated → preferred pairs
  - `unseenuniversity/rules/database` — Postgres-only / no-SQLite
  - `unseenuniversity/rules/coding` — OOP-first
  - `unseenuniversity/rules/igor-constraints` — no speculative flags
  - `unseenuniversity/rules/memory` — no new memory tables
  - `unseenuniversity/rules/docs-live-in-code` — top-of-file docstring on load-bearing
- **Prior watch-for notes**: `unseenuniversity/audits/smell/watch_next/*`.

---

## Skip / gate logic

`audit-smell` does NOT run when:
- Diff is doc-only (`*.md`, `*.txt`, `docs/`).
- Ticket carries a **justified** `audit-skip-smell` tag. The tag is only
  honoured here if audit-ticket check 15.5 granted it (doc-only, or a stated
  reason on a code-touching ticket). A bare self-declared tag on a code-touching
  ticket is stripped at filing and must NOT skip the audit — the author does not
  control their own exemption (gate-attack G5, T-audit-smell-evasion-guards).
- The **cumulative** changed-line count for the *ticket* is below the threshold
  (default: <30 lines AND no new files). Override via `AUDIT_SMELL_MIN_LINES`.

**Cumulative, not per-run.** The threshold applies to the SUM of changed lines
across every commit made for this ticket, not the single diff in front of you.
A change sliced into three 15-line commits each passes a per-run threshold while
accumulating 45 lines of unreviewed rot — the exact slicing attack G5 describes.
Compute the ticket total from its commits, e.g.
`git log --oneline --grep "<ticket-id>" --format=%H | xargs -r git show --stat`
(or the sprint's per-ticket line tally), and gate on that sum. Once a ticket's
cumulative diff crosses the threshold, audit-smell runs for the current commit even
if this commit alone is small — the exemption does not reset per commit.

Tickets can opt up via `audit-emphasis-smell` tag → run with stricter
thresholds and Opus model.

---

## The 17 Checks

Each check has:
- **Detector**: how the helper engine spots the violation
- **Severity**: LOW / MED / HIGH (drives AMEND vs warn)
- **AMEND**: the message returned to the sprinter

Checks 1–13 are diff-local; checks 14–17 are diff-plus-context.

### Check 1 — Bare try/except without log line

**Detector**: AST scan for `Try` nodes whose `except` body has no call
matching `self.log.*` / `log.*` / `logger.*` / equivalent base-class log
methods. Empty except + bare `pass` is the canonical hit.

**Severity**: HIGH (silent failure mode hides everywhere)

**AMEND**: "Bare except at `<file>:<line>` swallows errors silently. Add
`self.log.warning(...)` with surrounding state (what was being attempted,
what fell through). See `unseenuniversity/rules/inherit-base-class` for the log
handle that comes from inheritance."

### Check 2 — Silent-return-False / fanout=0 without WARNING

**Detector**: Functions whose body contains `return False` / `return None`
/ `return []` / `return 0` paths that are NOT immediately preceded by a
`self.log.warning(...)` call. Especially in scope_guard / pe_chain /
inhibition / dispatch sites where fanout=0 is meaningful.

**Severity**: HIGH (these are exactly where bugs hide for weeks)

**AMEND**: "Silent return at `<file>:<line>:<function>` discards a path
without a log breadcrumb. Add `self.log.warning(<reason>, ...state...)`.
Required by the logging-as-debug-base feedback rule."

### Check 3 — Names describe shape not purpose

**Detector**: New variable / function / parameter names matching the
shape-not-purpose vocabulary: `tmp`, `temp`, `data`, `info`, `result`,
`val`, `obj`, `process`, `do_thing`, `handle_it`, `helper`, `utils`,
`do_stuff`, `value`, `item` (when not in a clear iteration), `x`/`y`/`z`
outside coordinate contexts. Excludes test-fixture and stdlib-conventional
names (`self`, `cls`, `args`, `kwargs`, etc.).

**Severity**: MED

**AMEND**: "`<name>` at `<file>:<line>` describes shape, not purpose. Rename
to something that says what it IS — e.g., `<suggested rewrite from
context>`."

### Check 4 — Dead code

**Detector**:
- Unused imports (LibCST / pyflakes-style)
- Unreferenced module-level functions
- Commented-out code blocks ≥3 consecutive lines starting with `#`
- Bare `pass` in non-empty function bodies

**Severity**: MED

**AMEND**: "Dead code at `<file>:<line>`: `<what>`. Remove it. Don't keep
backwards-compat shims for code without callers."

### Check 5 — Comments explaining WHAT, not WHY

**Detector**: New comments where the next code line is trivially the same
as the comment (e.g., `# increment counter` followed by `counter += 1`).
Sonnet adds these reliably. Heuristic: comment text matches the next-line
identifiers and verbs ≥60%.

**Severity**: LOW (warn, not block)

**AMEND**: "Comment at `<file>:<line>` explains what the code does, not
why. Either delete or rewrite to capture WHY (constraint, invariant,
non-obvious reason)."

### Check 6 — Mocked DB in tests

**Detector**: New test functions that import or use `unittest.mock` /
`pytest-mock` to mock `db_proxy`, `psycopg2`, `Connection`, `cursor`, or
any helper that wraps Postgres. Integration tests in this project hit
real Postgres via `pg_test_schema` fixture.

**Severity**: HIGH (incident-rooted rule)

**AMEND**: "Mocked DB at `<file>:<line>`. Project rule: integration tests
hit real Postgres via the `pg_test_schema` fixture (T-test-postgres-schema).
Mocked-DB tests pass while migrations break in prod — the canonical
incident behind this rule."

### Check 7 — New feature flag without go-live-when companion

**Detector**: New `os.environ.get("IGOR_*_ENABLED")` / `os.getenv(...)`
references defaulting to off, OR new boolean config keys named with the
`*_ENABLED` / `feature_*` pattern, where the ticket has no companion
ticket tagged `go-live-when:<condition>`.

**Severity**: HIGH (rule from feedback memory: no-speculative-flags)

**AMEND**: "New feature flag `<name>` at `<file>:<line>` without a
go-live-when companion. Project rule: build to intent. If the gate is
real, file a companion ticket `T-go-live-when-<condition>`; otherwise
remove the flag and ship to intent."

### Check 8 — New memory table

**Detector**: AST scan for `CREATE TABLE` / migration-runner calls
introducing tables OR new SQLAlchemy / dataclass models intended for a new
persistence store, instead of the canonical flat-file memory store
(`devlab/runtime/memory/`) or Postgres for shared device state.

**Severity**: HIGH (rule violation: no-new-memory-schemas)

**AMEND**: "New memory store `<name>` at `<file>:<line>`. Project rule:
memory distinctions become tags/subdirs in the flat-file store
(`devlab/runtime/memory/<category>/`), not new schemas. See `unseenuniversity/rules/memory`."

### Check 9 — SQLite / file-store fallback

**Detector**: New imports of `sqlite3`, `aiosqlite`, `tinydb`,
`pickleDB`; or new file paths matching `*.db`, `*.sqlite`, `*.sqlite3`
used as a backend; OR phrases in code/comments proposing fallback
storage. Negation constructions ("no sqlite", "without fallback") pass.

**Severity**: HIGH (palace rule violation: postgres-only)

**AMEND**: "SQLite / file-store at `<file>:<line>`. Project rule:
Postgres only, no fallbacks, no dual paths. See `unseenuniversity/rules/database`."

### Check 10 — Backwards-compat shim without callers

**Detector**: New functions / methods marked with `# legacy` /
`# deprecated` / `# back-compat` comments OR named with `_legacy` /
`_old` / `_deprecated` suffix where ripgrep finds zero callers in the
repo.

**Severity**: MED

**AMEND**: "Backwards-compat shim `<name>` at `<file>:<line>` has no
callers. Don't keep shims for code without callers. Either point at the
caller or delete."

### Check 11 — Speculative abstraction

**Detector**: New abstract base classes / Protocols / strategy patterns
introduced with only ONE concrete implementation in the diff. Heuristic
threshold: `class X(ABC)` or `class X(Protocol)` with exactly 1 sibling
implementation in the same diff.

**Severity**: LOW (warn, not block — judgment call)

**AMEND**: "Abstract `<name>` at `<file>:<line>` introduced with one
concrete implementation. Three similar lines is better than a premature
abstraction. Either name the second implementation that's coming or
inline the concrete shape."

### Check 12 — Class without base-class inheritance

**Detector**: AST scan via `devlab/claudecode/audit_check_igorbase.py`
(existing tool). Reports new class definitions in `unseen_university/devices/` whose
bases don't include `BaseDevice` / `BaseShim` and aren't in
`THIRD_PARTY_BASES` (Pydantic, Enum, ABC, Protocol, dataclass, etc.).

**Severity**: HIGH (palace rule: inherit-base-class)

**AMEND**: "Class `<name>` at `<file>:<line>` doesn't inherit from
BaseDevice (device code) or BaseShim (shim code). The base class IS
the logging+introspection layer. See `unseenuniversity/rules/inherit-base-class`."

### Check 13 — Shared state across functions without encapsulation

**Detector**: Module-level mutable state (dicts, lists, sets) read OR
written by ≥2 module-level functions in the diff, where no class wraps
them. Excludes constants (frozen / Final-typed) and shared singletons.

**Severity**: MED (oop-first inverse)

**AMEND**: "Module-level shared state `<name>` at `<file>:<line>` is
read/written by `<funcs>`. Project rule: shared state across functions
proposes a class. See `unseenuniversity/rules/coding`."

### Check 14 — Top-of-file docstring stale on load-bearing edit

**Detector**: Diff touches a file whose path matches the load-bearing
list from `unseenuniversity/subsystem_index/<area>` AND the file's top-of-file
docstring (first triple-quoted block) is unchanged in the diff.

**Severity**: HIGH (docs-in-code rule)

**AMEND**: "Load-bearing file `<file>` edited but top-of-file docstring
unchanged. Update the docstring to reflect what changed. See
`unseenuniversity/rules/docs-live-in-code`."

### Check 15 — Deprecated forms from preferred_paths

**Detector**: For each child of `unseenuniversity/rules/preferred_paths/`, check
the diff for the `deprecated` pattern. Each match flags an AMEND with
the `preferred` form named.

**Severity**: HIGH (project-level Sonnet-failure rule)

**AMEND**: "Deprecated form `<deprecated_pattern>` at `<file>:<line>`.
Use `<preferred_pattern>` instead. Reason: `<why from palace node>`. See
`unseenuniversity/rules/preferred_paths/<entry>`."

### Check 16 — Fix-one-leave-many (call-graph walk)

**Detector**:
1. Identify signature changes in the diff (function definitions whose
   parameter list, name, or return shape changed).
2. For each changed signature, ripgrep all callers in the repo (excluding
   the diff itself).
3. For each caller NOT touched by the diff, flag.
4. Override conditions: ticket title or description includes
   "first of N" / "phase 1 of N" / explicit scope marker, OR the changed
   signature was added (no prior callers existed).

**Severity**: HIGH (Sonnet's signature failure mode)

**AMEND**: "Signature change to `<name>` at `<file>:<line>`. Found
`<N>` caller(s) NOT touched by this diff: `<file>:<line>`,
`<file>:<line>`, ... Either update them in this diff OR mark the ticket
as 'first of N' explicitly. The proxy patterns (db_proxy, inference
gateway) exist because this failure mode wasn't caught at edit time."

### Check 17 — Diff drift

**Detector**: Compare the diff's touched files against the ticket's
`Affected files` list. Files in the diff but not in the ticket are
drift.

**Severity**: HIGH — AMEND-by-default. Drift requires explicit ticket
extension; not a judgment call.

**AMEND**: "Diff touches files not in the ticket's `Affected files`
list: `<file>`, `<file>`. Drift requires explicit ticket extension —
either narrow the diff to the ticket's scope OR amend the ticket
description to include these files (with rationale) before commit."

---

## Output shape

### PASS

```
audit-smell: PASS
Diff: <N> files, <M> lines changed
Checks: 17/17 passed
Telemetry: unseenuniversity/audits/smell/runs/<timestamp>
```

### AMEND

```
audit-smell: AMEND
Diff: <N> files, <M> lines changed
Checks: <P>/17 passed; <Q> AMEND (<R> HIGH, <S> MED, <T> LOW)

HIGH severity (block commit):
  Check <#> — <name>: <one-line summary>
    File: <file>:<line>
    Fix: <suggested action>
  ...

MED severity (review before commit):
  ...

LOW severity (warn):
  ...

Telemetry: unseenuniversity/audits/smell/runs/<timestamp>
```

When invoked from `/sprint`: any HIGH AMEND blocks `git commit` until
fixed or explicitly overridden inline. MED items surface for review;
sprinter may proceed at their judgment. LOW items are warnings only.

---

## Steps

### 1. Determine if audit fires

Apply the skip/gate logic. If skipped, write a minimal run record
(`level: smell, skipped: <reason>`) and return PASS immediately.

### 2. Read inputs

Diff (staged or working-tree), ticket (from `cc_queue.py show <id>`),
palace context (the rules listed above).

### 3. Read prior watch-for notes

```
memory_get(path="unseenuniversity/audits/smell/watch_next/<id>")
```

For each active note, check whether this diff matches the watch
condition. Hits get logged in the run record.

### 4. Run the 17 checks

Each check produces zero or more findings. Findings carry severity,
file:line, matched_pattern, and the AMEND message. Use the helper
engine at `devlab/claudecode/audit_smell_engine.py` for AST / regex
scans.

Stop-on-first-fail is NOT enabled — run all 17 even if early checks
fail, so the AMEND output is complete in one round.

### 5. Aggregate verdict

- Any HIGH finding → AMEND, blocking
- Only MED findings → AMEND, advisory
- Only LOW findings → PASS (with warnings printed)
- No findings → PASS

### 6. Write watch-for notes

When a check passes only marginally (e.g., diff drift on 1 file with a
plausible explanation), write a watch-for note at
`unseenuniversity/audits/smell/watch_next/<id>` with TTL 14 days.

### 7. Emit run record

Per the standard shape (`unseenuniversity/audits/smell/runs/<timestamp>`).

### 8. Return verdict

PASS or AMEND, in the output shape above. /sprint gates `git commit`
on this return for HIGH severity findings.

---

## Helper engine: devlab/claudecode/audit_smell_engine.py

The skill's procedural shell delegates to a Python helper for the
AST-heavy checks. The helper provides a single class
`SmellEngine(BaseDevice)`:

```python
class SmellEngine(BaseDevice):
    """Runs the 17 audit-smell checks against a diff + ticket context."""

    def __init__(self, diff: str, ticket: dict, palace_ctx: dict):
        super().__init__()
        self.diff = diff
        self.ticket = ticket
        self.palace = palace_ctx

    def run_all(self) -> list[Finding]:
        """Run all 17 checks, return findings sorted by severity desc."""

    # One method per check
    def check_bare_except(self) -> list[Finding]: ...
    def check_silent_return_false(self) -> list[Finding]: ...
    def check_shape_names(self) -> list[Finding]: ...
    def check_dead_code(self) -> list[Finding]: ...
    def check_what_comments(self) -> list[Finding]: ...
    def check_mocked_db(self) -> list[Finding]: ...
    def check_speculative_flag(self) -> list[Finding]: ...
    def check_new_memory_table(self) -> list[Finding]: ...
    def check_sqlite_fallback(self) -> list[Finding]: ...
    def check_backcompat_shim(self) -> list[Finding]: ...
    def check_speculative_abstraction(self) -> list[Finding]: ...
    def check_base_class_inheritance(self) -> list[Finding]: ...
    def check_module_shared_state(self) -> list[Finding]: ...
    def check_stale_docstring(self) -> list[Finding]: ...
    def check_preferred_paths(self) -> list[Finding]: ...
    def check_fix_one_leave_many(self) -> list[Finding]: ...
    def check_diff_drift(self) -> list[Finding]: ...
```

`Finding` is a frozen dataclass: `(check, severity, file, line,
matched_pattern, amend_message)`.

The helper inherits from `BaseDevice` per
`unseenuniversity/rules/inherit-base-class`. (Yes, the smell-checker class
inherits from the base class it checks for. That's intentional — the
audit should pass its own checks.)

Existing checks already implemented standalone get reused, not
re-implemented:
- Check 12 (base-class inheritance) wraps
  `devlab/claudecode/audit_check_igorbase.py`.
- Check 9 (SQLite) wraps `devlab/claudecode/audit_check_sqlite_imports.py`.
- Check 1 (bare except) wraps `devlab/claudecode/audit_check_bare_except.py`.

The engine and its tests land when this skill ships. Until then, the
SKILL.md describes the shape and downstream tickets fill in.

---

## Override

When Akien explicitly overrides a HIGH AMEND inline ("ignore Check 17 —
diff drift was authorized in the prior turn"), record the override:

```yaml
findings:
  - check: 17
    severity: high
    overridden: "drift authorized: file added per turn-of-conversation, ticket extended verbally"
```

Overrides are NOT failures — they're informed proceedings. The record
lets `audit-audits` spot patterns (a check overridden 50% of the time
is mis-shaped).

---

## Hard rules

- Always run all 17 checks; don't stop on first fail.
- HIGH findings block `/sprint`'s commit step until fixed or overridden.
- MED findings surface for review; sprinter judges proceed-or-fix.
- LOW findings are warnings; they DO NOT block.
- Always emit a run record (or document the verdict equivalently until
  the telemetry helper lands).
- The smell engine itself inherits from BaseDevice (eat your own
  dogfood per `unseenuniversity/rules/inherit-base-class`).

---

## Standalone invocation

```
/audit-smell                        # working-tree diff
/audit-smell --staged               # staged diff
/audit-smell --diff <ref>...HEAD    # arbitrary range
```

Useful before committing ad-hoc work outside `/sprint`, or for spot
checks of areas after Igor's autonomous edits.

---

## Why Sonnet (not Haiku)

Several checks need judgment that pattern-matching alone misses:
- "Names describe shape not purpose" — context-dependent. A `result`
  variable in a 5-line function is fine; in a 200-line function it
  hides reading.
- "Speculative abstraction" — judgment call about when the second
  implementation is realistically near.
- "Comments explaining WHAT" — heuristics under-fire and over-fire;
  judgment closes the gap.
- Diff drift — sometimes drift is correct (related fix found while
  in-context); the AMEND is a check, not a verdict.

Haiku misses too many of these in practice. The cost-per-finding
math justifies Sonnet here. Tickets tagged `audit-emphasis-smell`
escalate further to Opus.
