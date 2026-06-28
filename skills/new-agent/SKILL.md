---
name: new-agent
description: Scaffold a new device from the template — asks 5 questions, produces unseen_university/devices/<name>/ and a config profile.
model: haiku
---

# /new-agent — Scaffold a new rack device

Asks 5 questions, then calls `python run scaffold` to produce:
- `unseen_university/devices/<name>/` — device.py, shim.py, __init__.py (from template)
- `unseen_university/config/profiles/<name>.yaml` — pre-populated profile

**OUT of scope:** auto-registration on the live rack, automatic test generation.

---

## Steps

### 1. Ask the 5 questions

Ask the user these questions in sequence. Wait for each answer before asking the next.

1. **Agent name** — slug, lowercase, hyphens OK (e.g. `my-sensor`, `budget-gate`)
2. **Purpose** — one sentence: what does this agent do?
3. **Rack services** — which devices will it talk to? (comma-separated, e.g. `inference,browser_use`) — or `none`
4. **Mode** — `long-running` (runs a subprocess) or `stateless` (no background process)?
5. **Extra pip dependencies** — comma-separated package names, or `none`

### 2. Run the scaffold

With all answers collected, call:

```bash
python run scaffold \
  --name "<answer 1>" \
  --purpose "<answer 2>" \
  --services "<answer 3>" \
  --mode "<answer 4>" \
  --deps "<answer 5>"
```

Note: hyphens in the name are automatically normalised to underscores for the Python
module directory (e.g. `test-agent` → `unseen_university/devices/test_agent/`). This is required for
valid Python imports.

### 3. Verify

Run both checks. Both must pass before reporting success.

```bash
pip install -e .
```

```bash
python -c "import unseen_university.devices.<python_name>.device, unseen_university.devices.<python_name>.shim; print('import OK')"
```

Where `<python_name>` is the answer-1 slug with hyphens replaced by underscores.

### 4. Report

Show the user:
- Directory created
- Profile path
- The two `Next steps` lines from the script output (fill in stubs)
- Reminder: register with the rack when ready (out of scope here)
