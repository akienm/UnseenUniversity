---
name: librarian-model-selection
description: Tuning guide for the Librarian's model tier assignments. Reference when changing which model handles a task type, adding a new model, or overriding tier routing for a session.
model: haiku
---

# Librarian model selection — tuning guide

The Librarian routes each inference request to a tier based on `task_type`.
All routing is driven by `model_config.yaml` in the same directory as this
file — edit the YAML, no code changes needed.

---

## Tier overview

| Tier | Name    | Default models              | When used |
|------|---------|-----------------------------|-----------|
| 0    | routine | qwen2.5:8b, llama3.2:3b     | Chat, channel replies, DB proxy results |
| 1    | heavy   | qwen2.5:32b, qwen2.5:72b    | Research, summarization, multi-step reasoning |
| 2    | cloud   | claude-haiku-4-5, claude-sonnet-4-6 | Complex reasoning, plan/code, escalation fallback |

---

## Task type → tier mapping

Defined in `model_config.yaml` under `task_type_tiers`. Anything not listed
defaults to tier 0. Current assignments:

```
tier 0 (routine): chat, reply, db_query, channel
tier 1 (heavy):   summarize, research, analyze, explain
tier 2 (cloud):   reason, plan, code
```

---

## How to change an assignment

Edit `model_config.yaml`. Example — move `analyze` to cloud tier:

```yaml
task_type_tiers:
  analyze: 2   # was 1 (heavy)
```

Restart the Librarian process for changes to take effect. No migration needed.

---

## How to add a new model option

Add to the tier's `models` list. The Librarian tries models in order and
falls through on failure:

```yaml
tiers:
  1:
    models:
      - name: qwen2.5:32b
        backend: ollama
      - name: deepseek-r1:32b   # new addition
        backend: ollama
```

Supported backends: `ollama` (local), `anthropic` (cloud API via
`ANTHROPIC_API_KEY`).

---

## How to add a new task type

Add a key under `task_type_tiers` with the tier number. The task_type
string must match what callers pass in the inference request's `task_type`
field:

```yaml
task_type_tiers:
  transcribe: 1   # new task type → heavy tier
```

---

## Session override (temporary)

To force a specific tier for the duration of a session without editing the
YAML, pass `tier_override` in the inference request args. This is
respected by the Librarian's inference router and does not persist.

---

## Escalation path

When a tier-N model fails (timeout, unavailable, error), the Librarian
automatically escalates to tier N+1. Cloud (tier 2) is the terminal
fallback — if cloud fails, the request returns an error.

---

## Related files

- `model_config.yaml` — the authoritative routing config (edit this)
- `inference.py` — routing implementation (read-only reference)
