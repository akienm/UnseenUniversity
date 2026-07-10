---
name: intend
description: Capture an intention as a first-class present-tense contract and deconstruct it into hierarchical sub-intentions, each paired with its proof-obligation. The front boundary of the INTENTION -> DESIGN -> TICKET stack — use before /design when an intention needs capturing and breaking down.
---

# /intend — Capture + deconstruct an intention (front boundary)

The front edge of the settled `INTENTION -> DESIGN -> TICKET` stack
(architecture/workflow-levels; T-intention-capture-deconstruct-skill). Before
`/intend` there was nothing durable at the intention boundary — intentions lived
as hand-authored prose in `akien/outbox/IntentionsOutline.txt`. This skill turns
one into a durable artifact and **deconstructs** it into sub-intentions, each
carrying its **proof-obligation** — proof is a THREAD born here and carried
`intention -> ticket -> prereg -> prove`.

`IntentionsOutline.txt` stays the **Akien-authored SOURCE**
([[project_intentions_outline_is_source]]); this skill captures/deconstructs INTO
the artifact store and points back at the outline — it never overwrites it.

## Inputs

- The intention text (an "I intend that…" statement) or the conversation to distil
  one from. If given a topic, draft the present-tense statement and confirm it.

## Steps

### 1. Capture the global intention (present-tense contract)

An intention is a **present-tense contract** ([[project_intention_is_a_present_tense_contract]]),
not a goal. Assemble:
- **statement** — "I intend that …" (present tense, falsifiable-in-principle).
- **why** — the reason it's worth intending (there's always a why, CP3).
- **how_to_verify** — how we'd know it holds in reality (the seed of the thread).
- **constraints** — the hard constraints it must respect (list; `[]` if none).
- **intention_id** — `I-<kebab-slug>`.

Check coherence against existing intentions (the outline rule: *"intentions have to
be coherent with the existing intentions"*). Surface any conflict rather than
filing an incoherent intention.

### 2. Deconstruct into sub-intentions ⊗ proof-obligations

Break the global intention into **hierarchical sub-intentions**. Each sub-intention
is itself a present-tense contract AND **must carry its own proof-obligation** —
the concrete thing a build would have to prove to show that piece holds. This is
the non-evadable lever: `intention_emit.py` **refuses** a deconstruction whose
sub-intentions lack proof-obligations (a plan that can't say how each piece is
verified is a wish-list). Aim for ≥2 sub-intentions; one flat intention rarely
needs deconstructing.

Each `sub_intentions[]` entry: `{statement, why, proof_obligation}`.

### 3. Emit (validated in code)

```bash
cat > /tmp/intention_body.json <<'JSON'
{
  "intention_id": "I-<slug>",
  "statement": "I intend that <…>.",
  "status": "active",
  "date": "YYYY-MM-DD",
  "why": "<why it's worth intending>",
  "how_to_verify": "<how we'd know it holds>",
  "constraints": ["<hard constraint>", "..."],
  "sub_intentions": [
    {"statement": "I intend that <sub-A>.", "why": "<why>", "proof_obligation": "<what a build must prove>"},
    {"statement": "I intend that <sub-B>.", "why": "<why>", "proof_obligation": "<what a build must prove>"}
  ],
  "related": {"intentions": ["I-..."], "memories": ["project_..."]},
  "origin": "<outline line / conversation this distils>",
  "text": "# I-<slug>\n<statement>\n\n## Why\n<why>\n\n## How to verify\n<how_to_verify>\n\n## Sub-intentions\n- <sub-A> (proof: <obligation>)\n- <sub-B> (proof: <obligation>)"
}
JSON
python3 "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/intention_emit.py" \
  --deconstructed --body-file /tmp/intention_body.json \
  --produced-by "human:akien"
```

- **Capture-only** (base tier, no deconstruction yet): drop `--deconstructed` and
  the `why`/`how_to_verify`/`constraints`/`sub_intentions` fields — only
  `intention_id` + `statement` are required. Deconstruct later by re-emitting the
  same `intention_id` (reuse its `--stamp`) with `--deconstructed`.
- `--produced-by` is the backward edge: `human:akien` when Akien stated it, else
  `session:cc.0`.

### 4. Report

```
/intend I-<slug> — <statement>
Sub-intentions: <N> (each with a proof-obligation)
Coherent with: <existing intentions checked>
Source line reconciled to IntentionsOutline: <yes/na>
```

## Flow integration

```
/intend           → capture + deconstruct the intention (I-<slug>)
/design           → open a design realizing that intention (draft carries I-<slug>)
/sorted           → resolve forks, spawn tickets → the design; tickets thread the
                    sub-intentions' proof-obligations into prereg/prove
```

## Hard rules

- An intention is present-tense; a goal is retired ([[project_goals_being_retired]]).
- Every sub-intention carries its `proof_obligation` — the emitter refuses a
  deconstruction that doesn't (proof-as-thread; CP1/CP3).
- `IntentionsOutline.txt` is the Akien-authored source; capture INTO the store,
  never overwrite the outline.
- Intentions are append-only living entities ([[project_intentions_as_living_entities]]);
  update by re-emitting the same `intention_id` + `--stamp` (atomic overwrite),
  never a second node. An intention closes only when superseded (outline rule).
