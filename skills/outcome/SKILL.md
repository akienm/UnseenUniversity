---
name: outcome
description: Review a decision's hypothesis against observable evidence and record whether it confirmed, falsified, or needs more time. Triggered at last-ticket close or manually. This is what closes the learning loop.
model: sonnet
---

# /outcome — Hypothesis review at decision close

The learning loop has no value unless it closes. /outcome is the closing step —
it takes the hypothesis stated when the decision was filed and asks: did it hold?

Without /outcome, we ship indefinitely and accumulate done tickets without
knowing if the system is actually improving.

## When to run

- **Automatically prompted**: when the last ticket of a decision moves to closed/awaiting_validation, /sprint-ticket surfaces the prompt: "Last ticket of D-xxx just closed — run /outcome?"
- **Manually**: `/outcome D-xxx` at any time after the decision's work has had time to show results.
- **Weekly retro**: /weekly-retro surfaces un-reviewed decision hypotheses as a standing item.

## Invocation

```
/outcome D-xxx             — review hypothesis for a specific decision
/outcome                   — list decisions with unreviewed hypotheses
```

---

## Steps

### 1. Read the hypothesis

```bash
DECISION_FILE=$(ls "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}"/devlab/runtime/memory/decisions/*<D-id>*.json | head -1)
python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['body'].get('text',''))" "$DECISION_FILE" \
  | grep -A 20 "## Hypothesis"
```

If the decision predates hypothesis tracking (no `## Hypothesis` section), note that and skip to a general outcome assessment.

### 2. Gather observable evidence

Based on the measurement signal stated at /sorted time, collect current data. Common patterns:

```bash
# Whatever the hypothesis named — read it from the canonical substrate.
# Device logs (canonical hierarchy ~/.unseen_university/logs/<device>/<stream>/):
grep -rh "HYPOTHESIZE\|<signal>" ~/.unseen_university/logs/*/info/ 2>/dev/null | tail -50

# Tickets / decisions / notes — grep the flat-file memory store directly:
grep -rl "<signal>" "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/" 2>/dev/null
```

Use whatever data source the hypothesis named. If no specific source was named, use the most relevant observable proxy.

### 3. Assess the verdict

Compare the hypothesis claim against the evidence. Choose one:

- **confirmed** — evidence clearly supports the claim.
- **partially_confirmed** — some evidence supports it; other aspects didn't move or are unclear.
- **falsified** — evidence contradicts the claim. The expected change didn't happen.
- **too_early** — insufficient time has passed or data accumulated to tell. Set a re-check date.
- **inconclusive** — the measurement signal named at hypothesis time wasn't available or was ambiguous.

### 4. Write verdict to decision record

Re-emit the decision JSON with an `## Outcome` section appended to `body.text`,
reusing the file's existing stamp so it's an atomic in-place overwrite — never a
second decision node (D-canonical-memory-consolidation-2026-06-23). Fill the
verdict fields (from Step 3) into `OUTCOME` before running:

```bash
DECISION_FILE=$(ls "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}"/devlab/runtime/memory/decisions/*<D-id>*.json | head -1)
OUTCOME=$(cat <<EOF

## Outcome — $(date +%Y-%m-%d)
**Verdict:** <confirmed / partially_confirmed / falsified / too_early / inconclusive>
**Evidence:** <1-3 sentences summarizing what the data showed>
**Learning:** <one sentence: what does this outcome teach us about this kind of decision?>
**Re-check:** <if too_early: when to check again>
EOF
)
OUTCOME="$OUTCOME" python3 - "$DECISION_FILE" <<'PY'
import json, os, sys, subprocess
from unseen_university._uu_root import uu_root
TOOLS = str(uu_root() / "devlab" / "claudecode")
sys.path.insert(0, TOOLS)
from memory_emit import parse_filename
path = sys.argv[1]
rec = json.load(open(path))
body = rec["body"]
body["text"] = body.get("text", "") + os.environ["OUTCOME"]
body["outcome_date"] = __import__("datetime").date.today().isoformat()
open("/tmp/outcome_body.json", "w").write(json.dumps(body))
stamp = parse_filename(os.path.basename(path))["stamp"]   # reuse → atomic overwrite
subprocess.run([sys.executable, os.path.join(TOOLS, "memory_emit.py"),
    "--category", "decisions", "--emitter", rec["emitter"], "--kind", "decision",
    "--namespace", body["decision_id"], "--stamp", stamp,
    "--body-file", "/tmp/outcome_body.json"], check=True)
PY
```

The decision JSON now carries the outcome (Step 4 wrote `## Outcome` + `outcome_date`
into it). No second store to update — the flat-file record is canonical.

### 5. Surface to Akien

```
/outcome D-xxx — <verdict>
Hypothesis: "<hypothesis text>"
Evidence: <summary>
Learning: <learning>
```

If **falsified**: surface prominently. A falsified hypothesis is *good data* — it rules out a wrong bet. Ask: "Does this change any pending decisions or active tickets?"

If **too_early**: set a calendar note or slate entry for the re-check date.

---

## Steps — /outcome (list mode)

```bash
# Decisions in the flat-file store with no ## Outcome / outcome_date yet.
python3 - <<'PY'
import json, glob, os
root = os.environ.get("UU_ROOT", os.path.expanduser("~/dev/src/UnseenUniversity"))
rows = []
for f in glob.glob(f"{root}/devlab/runtime/memory/decisions/*.json"):
    b = json.load(open(f)).get("body", {})
    if not b.get("outcome_date") and "## Outcome" not in b.get("text", ""):
        rows.append((b.get("decision_id", "?"), b.get("title", "")))
for did, title in sorted(rows)[:20]:
    print(f"{did}: {title}")
PY
```

Print as:
```
Decisions with unreviewed hypotheses (N):
  D-xxx (filed YYYY-MM-DD): <title>
  D-yyy (filed YYYY-MM-DD): <title>
```

---

## Hard rules

- Never skip /outcome because the outcome is obvious — "obvious" outcomes written down are the ones that teach you the most when they turn out to be wrong later.
- Falsified is not failure. A falsified hypothesis is the system working correctly — it caught a wrong bet before it propagated.
- too_early is not a dodge — always pair it with a re-check date.
- Learning is mandatory, even one sentence. That's the compounding value.
