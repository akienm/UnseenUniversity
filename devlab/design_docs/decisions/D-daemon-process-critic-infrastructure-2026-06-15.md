# D-daemon-process-critic-infrastructure-2026-06-15

**title:** Unified daemon supervisor, process skill/script pairs, critic expansion

**date:** 2026-06-15

**status:** open

**spawned_tickets:** TBD (pre-audit)

## Decision narrative

Three interconnected infrastructure improvements:

1. **Unified daemon supervisor via Ground Loop:** Instead of individual device daemons, consolidate all daemons under a single Ground Loop supervisor. Design: `devices/*/groundloop/runme.py` as hot-reloadable modules. Ground Loop monitors for errors, renames broken modules to `.borkedpy` to exclude them. Daemon config (launch frequency, etc.) in YAML/JSON alongside. Supports Linux, Windows, macOS, Android. Simplifies daemon creation: "make a file, put it in the right place. That's it."

2. **Skill + Script process pairs:** Orientation, constraint-sorting, and other process steps get both `/skill` and script versions. Allows interactive testing: put any piece through a step, examine results for efficacy. Improves developer velocity and system refinement.

3. **Critic expansion:** Generalize adversarial reasoning beyond evaluator. Humans have built-in critics; apply that pattern more broadly to improve quality.

## Hypothesis

**Observable difference:** 
- Daemons consolidated under Ground Loop supervisor; new daemons created via file placement only.
- Process steps (orientation, constraint-sorting, etc.) accessible as both skills and scripts for experimentation.
- Critic applied in additional components (dispatcher, tier-cascade, constraint-normalizer, etc.).

**Signal:**
- Daemons working, consolidated via runme pattern.
- System quality improves; adversarial reasoning catches more issues.

**Goal link:** G-self-improving-system, G-simplification, G-resilience

## Open questions

- Should cron daemon also be driven by Ground Loop, or remain separate?
- Which process steps beyond orientation + constraint-sorting get skill/script pairs?
- Where is critic added first? (dispatcher, tier-cascade, constraint-normalizer, other?)
- How do we measure "system quality improves" from critic expansion?
