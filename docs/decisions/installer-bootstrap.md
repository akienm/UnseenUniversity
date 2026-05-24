# Decision: Installer Bootstrap Paradox

**date:** 2026-04-27  
**status:** resolved — v0 bridge chosen, Phase 4 target specified  
**decision_id:** D-adc-phase-0-2026-04-27

---

## The paradox

The installer is a device on the rack. The rack is what the installer installs.
Something *outside* UnseenUniversity must bootstrap v0.

---

## v0 bridge (immediate — Phases 1-3)

The existing `igor` bash launcher (`~/TheIgors/igor` + `first_start.py`) is the v0
bridge. It is:

- Already working and tested
- **Not** a device — it lives outside UnseenUniversity entirely
- Sufficient for Phases 1-3 development (local machine, Akien present)

No Phase 1-3 work touches the installer problem.

---

## Phase 4 target: `agentctl init`

The target installer is a proper device that ships as part of UnseenUniversity itself:

```bash
pip install UnseenUniversity
agentctl init
```

That command must:

1. Bootstrap the skeleton (MCP aggregator up, flat-file registry initialized)
2. Start the IMAP bus (Dovecot if present, Python stub as fallback for test envs)
3. Find or launch Postgres — check for running instance, offer Docker fallback if absent
4. Register the Postgres device and verify health rollup shows green
5. Print: `rack is up. Add devices with agentctl register.`

**Constraints (non-negotiable):**
- No Igor required — UnseenUniversity is Igor-independent
- Portable: Ubuntu + macOS (arm64 and x86)
- Works at work: no TheIgors dependencies, no `~/.TheIgors/` assumed present
- Single command from a clean machine (only prereq: Python ≥3.11 + pip)

---

## What "works at work" must accomplish

Starting from zero on a fresh ubuntu or macOS box:

1. `pip install UnseenUniversity` — no errors
2. `agentctl init` — skeleton up, IMAP running, Postgres found or Docker-launched
3. `agentctl status` — all registered devices show healthy
4. Optional: `agentctl register igor --home ~/TheIgors` — Igor joins the rack

---

## Related tickets

- `T-adc-portable-bootstrap` (Phase 1) — `agentctl init` implementation, gated on skeleton
- `T-adc-installer-design-call` (Phase 4) — design call after Phase 1 portable-bootstrap informs the gap analysis
- `T-adc-installer-device` (Phase 4, if needed) — the installer-as-device if complexity warrants it
