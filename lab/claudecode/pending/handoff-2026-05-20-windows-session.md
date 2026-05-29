# Windows Session Handoff — 2026-05-20

**From:** Windows Claude (Saint Charles MO)
**To:** Linux Claude (New Mexico)
**Purpose:** Full briefing on what happened, what was decided, and what to do next.

---

## What this session accomplished

This was a Windows porting session — bringing the ADC + TheIgors stack to full parity with
the Linux dev environment. Akien and I worked through it systematically.

### Tier 0: Environment setup

- Confirmed Python 3.12 on Windows; chose **uv** as the package manager for per-repo venvs
- Set up `.venv` in all three Python repos: UnseenUniversity, swadl, TheIgors
- `python-diagnostic-base-class` is Akien's own package; it's not on PyPI. Source is at
  `C:\automation\local\python_diagnostic_base_class\`. Linux instance published it to GitHub
  at `github.com/akienm/python_diagnostic_base_class`. pyproject.toml now references it via
  git URL:
  ```
  "python-diagnostic-base-class @ git+https://github.com/akienm/python_diagnostic_base_class.git"
  ```

### Tier 1: Windows backend + agentctl init

Implemented `WindowsBackend` in `devices/installer/backends.py`:
- Uses `shutil.rmtree` + `shutil.copytree` to mirror rsync --checksum --delete semantics
- `is_available()` checks `platform.system() == "Windows"`

Fixed `agentctl init` for Windows:
- `_detect_postgres()`: wrapped docker subprocess in try/except (FileNotFoundError, OSError)
  so missing docker doesn't crash init
- `_shell_profile()`: added Windows case returning PowerShell profile path
- `_write_env_var_to_profile()`: uses `$env:NAME = "value"` syntax on Windows
- Profile hint updated: Windows says "restart PowerShell or run `. $PROFILE`"
- Replaced unicode arrow → with ASCII -> (Windows cp1252 can't encode U+2192)
- `_link_superclaude()`: already skipped on Windows (was correct)

Result: `agentctl init` runs clean on Windows, deploys 35 skills to `~/.claude/skills/`.

### Tier 1: Skill → script migrations (Windows did these)

All three get a cross-platform Python `run` script (no extension, called as `python run`):

**skills/note/run** — logs text to notes.log + slate; SKILL.md updated to call `python run`

**skills/savestate/run** — `midstream` mode writes `## In-flight` to slate; `close` mode
also calls session_capture.py; SKILL.md updated to use `python run midstream|close`

**skills/skills-sync/run** — `status | diff | deploy | copy-to-repo <name>` actions;
replaces rsync/diff with Python shutil; SKILL.md updated

**skills/decided/SKILL.md** — all bash commands replaced with cross-platform Python one-liners:
- `date -Iseconds` → `python -c "from datetime import datetime; print(datetime.now().isoformat(timespec='seconds'))"`
- `date -d '+14 days'` → `python -c "from datetime import datetime,timedelta; print((datetime.now()+timedelta(days=14)).strftime('%Y-%m-%d'))"`
- `echo >> decisions_log.dsb` → Python one-liner using THEIGORS_HOME env var
- `echo >> slate` → Python one-liner using IGOR_HOME env var
- `rm -f design_mode.json` → Python Path.unlink(missing_ok=True)
- `grep $(date +%Y%m%d)` → Python one-liner
- `python3 cc_queue.py` → `python "${CC_WORKFLOW_TOOLS}/cc_queue.py"`

**skills/commit/SKILL.md** — `cd ~/TheIgors && source venv/bin/activate && python -m pytest`
replaced with `uv run pytest tests/ -x -q`

### Tier 2: Linux shipped these (commit 16846be)

- `skills/context-load/run` — `create-slate` + `debug-flag` (replaces bash heredoc + touch)
- `skills/sprint-ticket/run` — `test` (uv run pytest) + `done-slate <id> <summary>`
- context-load/SKILL.md steps 0.5+1 → `python run` calls
- sprint-batch/SKILL.md — venv activation removed, paths use THEIGORS_HOME env var
- sprint-ticket/SKILL.md — step 8 → `python run test`, step 11 → `python run done-slate`
- `bin/adc` + `bin/adc.ps1` — bootstrap wrapper (finds agentctl in PATH first, creates
  `.venv` only on fresh install)
- Fixed 2 stale tests: WindowsBackend and _shell_profile Windows assertions

### Env vars (canonical set, must be in every session)

```
IGOR_HOME       = ~/.TheIgors            (runtime state, logs, channel)
THEIGORS_HOME   = <path>/TheIgors        (repo root — Python source)
CC_WORKFLOW_TOOLS = <adc>/lab/claudecode  (cc_queue.py, session_capture.py)
IGOR_HOME_DB_URL = postgresql://igor:...  (Postgres; not set on Windows — no DB here)
PYTHONUTF8      = 1
```

`superclaude.ps1` now sets IGOR_HOME, THEIGORS_HOME, PYTHONUTF8 on launch.

---

## The insight that matters most

During the session, Akien said something that is a first-principles statement about how
this whole system should be built. He said: *"I'm only as good as my feedback loop."*

He then said: *"That one is far bigger than you know."*

Let me explain why I think he's right, and why this matters for everything we build:

Every skill in this system is compiled reasoning — it encodes what we learned from doing
the task the hard way, and short-circuits future effort. But compiled reasoning has a
**decay problem**: the environment changes, edge cases appear, the skill starts failing
silently, and because there's no feedback signal, no one knows. The compiled reasoning
rots. You keep running a skill that was right six months ago and wrong today.

The feedback loop is what prevents this. Specifically:
1. **Self-verification**: the skill checks its own output. If it can't tell whether it
   succeeded, it can't report failure.
2. **Observability**: the result is visible. Not just written to disk — visible in the
   session where the problem can be acted on.
3. **Failure surface**: errors come up, not down. Silent failure is the worst outcome
   because it looks like success.
4. **Context feedback**: the outcome re-enters the LLM's active context. Writing to a
   file and moving on is not feedback — the LLM has to read it back.
5. **Learning preservation**: when a skill is wrong and we fix it, the fix must be
   durable. If it lives only in conversational memory, it dies with the session.

This applies not just to skills but to the whole system:
- The rack server should verify its own restarts
- cc_queue.py add should confirm what it wrote
- agentctl init should verify the skills it deployed
- The audit skills (/audit-ticket, /audit-design) should themselves have verifiable outputs

The /audit-feedback skill is the formal enforcement of this principle. But the principle
itself should be the design criterion for every new component.

Akien wants to discuss this further on the Linux side where it's easier to capture tickets.
When he brings it up, think about: where else in the system is the feedback loop absent?
What tools currently fail silently? What compiled reasoning is probably stale but
undetected?

---

## Decision filed: D-audit-feedback-2026-05-20

**Summary:** Add /audit-feedback skill to enforce feedback-loop completeness in all skills.

**Decision stub:** `TheIgors/lab/design_docs/decisions/D-audit-feedback-2026-05-20.md`
(committed to TheIgors repo separately — or Akien will bring it manually)

**Tickets to file — run this on Linux:**
```bash
python lab/claudecode/cc_queue.py add lab/claudecode/pending/D-audit-feedback-2026-05-20-tickets.json
```

**Three tickets:**

`T-audit-feedback-build` (M, sprint, priority 0.7)
- Build skills/audit-feedback/SKILL.md + run script
- `run check <name>` emits JSON with per-property status (present/absent/unclear + evidence)
- `run check-all` runs on all skills, emits summary table
- SKILL.md interprets output, returns PASS or AMEND with specific additions
- Five properties: self-verification, observability, failure surface, context feedback,
  learning preservation
- Cross-platform Python only, no Postgres dependency

`T-audit-ticket-integrate-feedback` (S, sprint, gate: T-audit-feedback-build, priority 0.5)
- Add a step to skills/audit-ticket/SKILL.md
- Detect if 'Affected files' contains any `skills/<name>/` path
- If yes, run /audit-feedback on each affected skill
- AMEND from /audit-feedback adds to audit-ticket AMEND list (doesn't auto-block)

`T-consequence-audit-feedback` (S, gate: 2026-06-03, priority 0.3)
- Observation ticket: watch for AMEND fatigue, false positives, integration overhead
- Check after 5 skill-touching tickets have gone through the new /audit-ticket flow

---

## Still outstanding — prioritized

**1. T-audit-feedback-build** — sprint it first; it's the enabling piece for everything
   downstream. M ticket, well-scoped, no Postgres dependency, fully cross-platform.

**2. context-load steps 2a+** — psql hash-gated rules load. Currently Linux-only because
   it shells out to psql. Low urgency until the psql portability problem is solved
   (probably part of the self-installer work).

**3. autocompact Windows shim** — deferred. tmux works on Linux; Windows needs pywinauto
   or equivalent to send keystrokes to a terminal. This goes in `shims/` not `skills/`.
   Akien confirmed swadl has pywinauto already, so the library is available.

**4. ADC self-installer docs** — `bin/adc` and `bin/adc.ps1` exist but nothing explains
   the bootstrap flow to a new user. Small S ticket worth filing:
   - What is adc, how to run it
   - What agentctl init does
   - How to set env vars on first launch
   - What skills-sync does after init

**5. Tier-2 skill migrations remaining** — context-load, sprint-batch, sprint-ticket are
   partially done (Linux did these). Check what's left vs what runs clean on Windows.

**6. Postgres on Windows** — most DB-dependent skills will error without it. Part of the
   self-installer story. Install psql as part of setup.

---

## Files changed this session (Windows commits)

Committed in `d811b0f` (Windows) and `16846be` (Linux, already in main):
- `devices/installer/backends.py` — WindowsBackend implemented
- `UnseenUniversity/cli/agentctl.py` — Windows init fixes (4 changes)
- `pyproject.toml` — pyyaml, mcp, python-diagnostic-base-class added
- `bin/superclaude.ps1` — IGOR_HOME, THEIGORS_HOME, PYTHONUTF8 exports added
- `skills/note/SKILL.md` + `skills/note/run` — migrated
- `skills/savestate/SKILL.md` + `skills/savestate/run` — migrated
- `skills/skills-sync/SKILL.md` + `skills/skills-sync/run` — migrated
- `skills/decided/SKILL.md` — bash → Python one-liners
- `skills/commit/SKILL.md` — venv activation → uv run

---

## How to pick this up on Linux

```bash
cd ~/UnseenUniversity   # or wherever your ADC clone lives
git pull origin main

# File the /audit-feedback tickets:
python lab/claudecode/cc_queue.py add lab/claudecode/pending/D-audit-feedback-2026-05-20-tickets.json

# Deploy updated skills to ~/.claude/skills/:
agentctl skills deploy

# Read this file for context, then sprint T-audit-feedback-build
```
