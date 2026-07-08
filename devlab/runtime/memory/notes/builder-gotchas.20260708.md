# Builder operational gotchas — the mechanics a fresh builder pays for twice

Mirrored from instance-local builder memory (T-builder-memory-repo-residency,
rebuildability-diff F2). These are not laws (those live in `rules/`) — they are the
operational mechanics that cost real retries/incidents to learn. Each carries its why.
produced_by: T-builder-memory-repo-residency

## proof_emitter — clean red→green (each of these cost a retry)

- `--test` must be ONE pytest node (`tests/x.py::test_y`), not a file — bare file → NoTestCollected.
- Run with the venv active — it shells `python -m pytest`; no venv → "No module named pytest".
- HEAD must BE the implementation commit — it inverts HEAD's parent..HEAD diff in place for
  red; a chore commit on top pushes the impl out of range → "passes in pre-implementation state".
- Net-new code needs stub-first: reverting a brand-new module gives ImportError, which is
  REJECTED as collateral red (must be AssertionError). Commit an importable stub first, real
  impl second — red then reverts impl→stub and fails on the assertion.
- The test+checker must land in a commit BEFORE the artifact commit, or the red inversion
  removes the test itself (collateral). (Re-confirmed 2026-07-08 on T-rules-store-materialize.)
- Deletions work (files are restored for red), but one deletion commit can't bind two tickets.
- Tests that grep the tree must exclude the proof-test file itself (its fixture literals match
  once tracked).
- Clean tree required; an untracked proof JSON from a prior run counts as dirty.
- A green proof proves YOUR intention only — re-run the changed module's own suite AND any
  repo-wide guard tests before closing (a rewrite once passed its proof while breaking its
  existing 18-test suite).

## proof_emitter — what it CANNOT prove (close shipped-unproven, name the lever)

- Pure structural refactors: natural red is ImportError (rejected); forcing an in-place
  inversion of a 1000-file reorg is corruption risk. Lever: a structural proof mode
  (import-resolution / top_level snapshot) decoupled from assertion-red.
- Regression fixes: red-at-pre-implementation finds the pre-regression state already green;
  the authentic red is an intermediate regressing commit the emitter can't target. Lever: a
  proof mode that reds against a named regressing commit.
- Verify such work green by other means (committed passing test, `pip install -e .`,
  cold-start import, straggler greps), then `--shipped-unproven` naming the lever precisely.

## Clone-based validation — the editable-finder trap

`pip install -e .` drops a MetaPathFinder that resolves `unseen_university` to the ORIGINAL
working tree and BEATS PYTHONPATH — tests run inside a git clone import the un-edited
original. Direction of hazard: normal red→green tickets false-RED (bounced, not mis-closed);
only already-green-at-HEAD (refactor-type) tickets can genuinely fake-green. Isolate with a
fresh venv installing the clone, or a subprocess that strips the `__editable__` finder.
PYTHONNOUSERSITE does not help. See `devices/aider/runner.py::_run_tests`.

## Repo-wide sweeps — scope traps that pass a hollow guard

- Never scope the footprint grep to a few dirs — `git grep -nE '<pat>'` with NO path scope
  first, then exclude deliberately (a sweep once missed 4 live `skills/*/run` scripts).
- Never scope the guard test to `*.py` — skill executors are extensionless `run` scripts;
  a `*.py` guard went green on an incomplete sweep. Guard ALL tracked files, excluding only
  `:!tests/ :!*.md :!devlab/runtime/memory/`.

## Ticket store mechanics

- Tickets/decisions are ENVELOPES: content (`status`, `description`, `intention`, ...) lives
  under `body.*`. A raw edit setting top-level `status` silently no-ops (adds a stray key;
  `cc_queue show` keeps reading `body.status`). Use `cc_queue.py setstatus`, or edit
  `d["body"][...]` and verify through `ticket_store.list()`.
- `cc_queue.py list` silently omits tickets that `show` and the store have — for pre-file
  dedup, grep the store directly: `grep -rl "T-slug" devlab/runtime/memory/tickets/`
  (T-cc-queue-list-hides-tickets).
- Open design questions on a ticket: number them `Q1:`/`A1:` in the description (status
  handling per D-ticket-status-model: open questions are a description property of TRIAGE).
- A ticket designed in conversation files at status `sprint`, not `triage` (triage = not yet
  looked at).

## Session reliability (CC-instance, but every CC builder hits it)

- Past ~200K context, CC confabulates architecture confidently — the tell is asserting a
  substrate fact instead of saying "I don't know". Before asserting how a subsystem works,
  read `devlab/runtime/memory/architecture/` + the code and QUOTE it; if silent, say IDK and
  ask. Prefer a fresh session for substrate-heavy design.
- Flush durable state (slate, store, commits) at each milestone AS IT LANDS, not batched at
  turn-end — compaction fires at turn boundaries and races a late flush.
