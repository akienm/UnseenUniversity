---
name: audit-feedback
description: Check a skill (or all skills) for feedback-loop completeness. Returns PASS or AMEND with specific additions needed.
model: sonnet
---

# /audit-feedback — Feedback-Loop Completeness Audit

Every skill is compiled reasoning. Compiled reasoning has a decay problem: the skill
silently fails, and because there is no feedback signal, no one knows. The five
properties below are the minimum feedback loop a skill must have to detect and surface
that decay.

> "I'm only as good as my feedback loop." — Akien, 2026-05-20

## Usage

```
python run check <skill-name>    — structured JSON report for one skill
python run check-all             — summary table for all skills
```

## The Five Properties

1. **Self-verification** — does the skill verify its own output before reporting success?
   Evidence: post-write read-back, `assert`, `verify` call, or explicit verify step.

2. **Observability** — is the result visible to the user, not just written silently to disk?
   Evidence: `print(` in run script, or explicit surface/report step in SKILL.md.

3. **Failure surface** — is failure detected and reported, not swallowed?
   Evidence: `sys.exit(1)` on error, `except` block with print, `raise`, or explicit
   STOP/fail step.

4. **Context feedback** — does the outcome re-enter Claude's active context?
   Evidence: run script prints to stdout (CC reads it), or SKILL.md has a step that
   reads and reports the output — not just "write to file and move on".

5. **Learning preservation** — is a learned fix captured durably?
   Evidence: AMEND step that writes to SKILL.md, palace node, or /decided; or explicit
   "record fix" or "durable" language.

## Steps

### 1. Run the checker

```bash
python run check <skill-name>
```

This produces JSON:
```json
{
  "skill": "<name>",
  "properties": [
    {"property": "self-verification", "status": "present|absent|unclear", "evidence": "..."},
    ...
  ],
  "verdict": "PASS|AMEND",
  "missing": ["<property>", ...]
}
```

### 2. Review "unclear" entries (LLM judgment)

The run script reports `present` or `absent` based on pattern matching. When evidence
is weak or context matters (e.g., a print statement that only runs on the error path),
re-read the relevant section and apply judgment:
- Confirm `present` if the property is genuinely satisfied
- Downgrade to `absent` if the evidence is cosmetic

### 3. Return verdict

**PASS** — all 5 properties present. Output:
```
audit-feedback: PASS
Skill: <name>
Properties: 5/5 present
```

**AMEND** — one or more properties absent. Output:
```
audit-feedback: AMEND
Skill: <name>
Properties: <N>/5 present

AMEND items:
  self-verification: add a read-back or assert after the main write operation
    e.g. in run script: `assert output_path.exists()` or read back and verify shape
  failure-surface: add sys.exit(1) on error path and print the error before exiting
    e.g. `except Exception as e: print(f"error: {e}", file=sys.stderr); sys.exit(1)`
  learning-preservation: add an AMEND step to SKILL.md that describes how to record
    fixes durably — /decided for design changes, palace node for operational rules
```

AMEND items should be specific enough to apply without further design. When a property
is absent, name the exact pattern to add and where.

## Integration with /audit-ticket

When /audit-ticket processes a ticket whose Affected files includes `skills/<name>/`,
it calls `/audit-feedback` on each affected skill. AMEND findings from /audit-feedback
are added to the audit-ticket AMEND list (advisory — does not auto-block the ticket).

## Hard rules
- Run the checker before reporting results — never eyeball SKILL.md and guess.
- AMEND additions must be specific: file, location, exact pattern. Vague "add verification"
  is not actionable.
- The skill is self-referential: `python run check audit-feedback` should return PASS.
  If it doesn't after changes to this skill, fix it before shipping.
