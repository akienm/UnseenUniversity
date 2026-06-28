---
name: factory-create
description: Scaffold a new factory spec from 6 questions — produces unseen_university/config/factories/<name>.yaml ready for review and instantiation.
model: haiku
---

# /factory-create — Scaffold a factory spec

Asks 6 questions, then calls `python run scaffold` to produce:
- `unseen_university/config/factories/<name>.yaml` — pre-populated factory spec

**IN scope:** factory YAML spec from answers, spec validation.
**OUT of scope:** live instantiation (owner must approve first; then run `python run instantiate`).

---

## Steps

### 1. Ask the 6 questions

Ask the user these questions in sequence. Wait for each answer before asking the next.

1. **Factory name** — slug, lowercase, hyphens OK (e.g. `research-orca`, `budget-gate`)
2. **Description** — one sentence: what does this factory do?
3. **Owner ID** — comms:// address of the owner (default: `comms://akien/`; press Enter to accept)
4. **Member agent types** — comma-separated agent_type slugs that exist in `unseen_university/config/profiles/` (e.g. `librarian,scraps`)
5. **Eval rubric** — evaluator rubric ID (e.g. `R-factory-output`), or `none`
6. **Daily budget USD** — daily budget limit (e.g. `5.00`), or `none`

### 2. Run the scaffold

With all answers collected, call:

```bash
python run scaffold \
  --name "<answer 1>" \
  --description "<answer 2>" \
  --owner-id "<answer 3 or comms://akien/>" \
  --members "<answer 4>" \
  --eval-rubric "<answer 5 or none>" \
  --daily-budget "<answer 6 or none>"
```

### 3. Validate the spec

```bash
python run validate "unseen_university/config/factories/<name>.yaml"
```

Report the result. If validation fails, surface the error and ask the user to correct their answers.

### 4. Report

Show the user:
- Factory spec path
- Members listed
- Owner ID
- Next steps:
  1. Review `unseen_university/config/factories/<name>.yaml`
  2. Owner approves → `python run instantiate "unseen_university/config/factories/<name>.yaml"` (or the owner can approve in-channel)
