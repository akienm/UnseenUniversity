# D-critic-skill-script-pair-2026-06-15

**title:** Critic as skill + script pair — testing/refining adversarial reasoning

**date:** 2026-06-15

**status:** open

**spawned_tickets:** T-critic-skill-implementation, T-critic-script-implementation, T-critic-testing-harness, T-consequence-critic-skill-script

## Decision narrative

Create `/critic` as a callable skill and `scripts/critic.py` as a runnable script. Takes input: symbol/module/ticket/? (what to critique). Returns: adversarial analysis — what could go wrong, what assumptions are questionable, what's incomplete. Not yet integrated into build/dispatch system; purpose is experimentation and refinement. Both interfaces (skill + script) allow testing from CLI, from code, interactively, and eventually from automated flows. Humans have built-in critics; we're making one explicit and testable.

**Interface (both skill and script accept same input):**
```
/critic <target>           # target = symbol name, module path, ticket ID, or free-text description
scripts/critic.py <target>
```

Output: structured critique (assumptions, gaps, risks, suggestions).

## Hypothesis

**Observable difference:** Critic skill and script available and working; CC can run `/critic <x>` or `python scripts/critic.py <x>` to get adversarial analysis on any target. Not yet wired into the build system or decision-making, but ready for experimentation.

**Signal:** Skill and script both callable; output is useful (actually finds real issues, not boilerplate); CC+Akien can iterate on prompt/approach without touching build system yet.

**Goal link:** G-self-improving-system (adversarial reasoning as a first-class tool)

## Open questions (none — design confirmed)
