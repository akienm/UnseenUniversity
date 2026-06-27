---
name: weekly-retro
description: 5-minute Friday retrospective — reviews hypothesis confirmation rate and what changes about next week's priorities. Called automatically by day-close on Fridays. Also callable standalone. Output to devlab/runtime/memory/notes/.
model: sonnet
---

# /weekly-retro — Friday hypothesis review

Day-close covers code health. Expert audit covers discipline health.
Weekly-retro covers the question neither asks: **are we making the right bets?**

5 minutes. Three questions. One screen of output.

## When to run

- Automatically: triggered by day-close when `date +%u` = 5 (Friday)
- Manually: `/weekly-retro` any time

---

## Steps

### 1. Pull this week's decision outcomes

```bash
python3 - <<'PY'
import json, glob, os, datetime
root = os.environ.get("UU_ROOT", os.path.expanduser("~/dev/src/UnseenUniversity"))
cut = datetime.date.today() - datetime.timedelta(days=7)
reviewed, unreviewed = [], []
for f in glob.glob(f"{root}/devlab/runtime/memory/decisions/*.json"):
    b = json.load(open(f)).get("body", {})
    did, title, txt = b.get("decision_id", "?"), b.get("title", ""), b.get("text", "")
    od = b.get("outcome_date")
    if od:
        try:
            if datetime.date.fromisoformat(od) >= cut:
                verdict = next((l for l in txt.splitlines() if "Verdict" in l), "").strip()
                reviewed.append((od, did, title, verdict))
        except ValueError:
            pass
    elif "## Outcome" not in txt:
        unreviewed.append((did, title))
print("# Outcomes recorded this week:")
for od, did, title, verdict in sorted(reviewed, reverse=True):
    print(f"  {did} ({od}): {title} — {verdict}")
print("# Closed/open with no outcome yet:")
for did, title in sorted(unreviewed):
    print(f"  {did}: {title}")
PY
```

### 2. Answer the two questions

**Q1 — Hypothesis confirmation rate this week**
Count outcomes: confirmed + partially_confirmed vs. falsified + inconclusive.
If >50% falsified or inconclusive: flag — we may be designing against wrong assumptions.
If no outcomes recorded: flag — the outcome loop isn't closing.

**Q2 — What changes about next week?**
Based on Q1: should any priorities shift? Any decisions that look wrong in light of this week's outcomes? Any intentions that should be revisited?
This is the one synthesis question. It doesn't require a long answer — one sentence is enough.

### 3. Surface unreviewed hypotheses

List decisions that have shipped (all tickets closed) but /outcome hasn't been run:
```
Needs /outcome: D-xxx (shipped N days ago), D-yyy (shipped M days ago)
```
Flag any that are >14 days overdue.

### 4. Write the retro to the memory store

Emit the retro as a note in the flat-file store (`devlab/runtime/memory/notes/`):

```bash
RETRO=$(cat <<EOF
## Week ending $(date +%Y-%m-%d)

### Hypothesis confirmation rate
${q1_summary}

### Priority changes for next week
${q2_summary}

### Needs /outcome
${overdue_outcomes:-none}
EOF
)
RETRO="$RETRO" python3 - <<'PY'
import os, json, subprocess, sys, datetime
from unseen_university._uu_root import uu_root
stamp = datetime.date.today().strftime("%Y%m%d")
body = {"title": f"weekly-retro {datetime.date.today()}", "text": os.environ["RETRO"]}
open("/tmp/retro_body.json", "w").write(json.dumps(body))
TOOLS = str(uu_root() / "devlab" / "claudecode")
subprocess.run([sys.executable, f"{TOOLS}/memory_emit.py", "--category", "notes",
    "--emitter", "cc.0", "--kind", "note", "--namespace", f"weekly-retro-{stamp}",
    "--body-file", "/tmp/retro_body.json"], check=True)
PY
```

### 5. Report

```
/weekly-retro — week ending YYYY-MM-DD
Outcomes this week: N confirmed, M falsified, P too_early, Q pending
Next week: <priority changes>
Needs /outcome: <list or "none">
```

---

## Hard rules

- Always run even if there are no outcomes this week — a zero-outcome week is itself a signal.
- If no outcomes were recorded in 2+ weeks, surface that directly: the outcome loop has stopped closing.
- Q3 (what changes) requires at least one sentence — "nothing changes" is an acceptable answer but must be stated.
