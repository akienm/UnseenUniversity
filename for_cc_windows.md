# for_cc — Windows box, aider builder bring-up

**You (a CC instance) are reading this on a Windows machine, probably with an old/unfamiliar
setup.** Akien put you here to stand up the **aider builder** and figure out how to run it on
Windows. This file is everything you need except the credentials (Akien hands you those, and —
good news — the first milestone needs none). Written 2026-07-06 by the CC.0 that built the device.

---

## The one-line mental model

There are **two layers**, and you almost certainly want only the first:

1. **The runner** — `unseen_university/devices/aider/runner.py`. Standalone, **bus-free**, stdlib-only.
   It clones a repo to a throwaway dir, cuts a work branch (never main), runs `aider` headless
   against Hex (ollama, $0), applies an objective gate (tests-green + diff-scope), and reports.
   **This is your target. Get this green first.**
2. **The rack device** — `device.py` / `shim.py` / `worker_listener.py`. Wraps the runner so
   Granny can dispatch tickets to it over the bus. Needs Postgres + the bus + `~/.granny/` flags.
   **Ignore this until the runner works.** It is main-box rack integration, not Windows bring-up.

If you find yourself installing Postgres or debugging the bus on day one, stop — you went to
layer 2 too early.

---

## What is DIFFERENT here (assumptions that will bite)

This repo grew up on Linux. Things that are implicitly true on the main box and are **not** here:

- **`~/.aider-venv/bin/aider` does not exist** — on Windows aider lives at
  `%USERPROFILE%\.aider-venv\Scripts\aider.exe`. The code already handles this
  (`consts.aider_bin()` checks `Scripts/aider.exe`), but only if you install aider where it expects.
- **`git` may not be on PATH.** The runner shells out to `git` for clone/branch/commit. If `git`
  isn't callable from your shell, nothing works. Install Git for Windows and confirm `git --version`.
- **`pip install -e .` will probably fight you** (psycopg2 and friends want a C toolchain). **You do
  not need it** — see the bring-up below. The runner runs from `PYTHONPATH` alone.
- **Hex is on the LAN, not localhost.** Default is `http://10.0.0.100:11434`. From a different box
  you must be able to reach it — `curl http://10.0.0.100:11434/api/tags` (or the PowerShell
  equivalent) must return the model list. If Hex moved, set `HEX_OLLAMA` accordingly.
- **Path separators / shells.** The code uses `pathlib` and `os.pathsep` throughout and never uses
  `shell=True` or bash-isms, so it's Windows-portable *by design* — but it was **proven on Linux
  only**. You are the one shaking out git-on-PATH, the venv layout, and Hex-over-LAN. Expect one or
  two small portability surprises and fix them at the source (don't hardcode a Windows path — use an
  env var; the knobs are all listed below).

---

## Prerequisites (install these first)

1. **Python 3.9+** (`py --version`). 3.10+ is nicer but the runner uses `from __future__ import
   annotations`, so 3.9 is fine.
2. **Git for Windows**, on PATH (`git --version`).
3. **This repo**, cloned somewhere, e.g. `C:\src\UnseenUniversity`. (You're reading this from its root.)
4. **Network reach to Hex** (`http://10.0.0.100:11434`). Confirm before anything else:
   ```powershell
   Invoke-RestMethod http://10.0.0.100:11434/api/tags | Select -Expand models | Select name
   ```
   You want to see `qwen3-coder:30b` and `devstral-small-2:24b` in the list.

---

## Bring-up (PowerShell)

Everything below is copy-paste. `$env:` is a session env var; use `[Environment]::SetEnvironmentVariable(...,'User')` to persist.

### 1. Install aider in its OWN venv (never mixed with UU)

aider is an **external dependency** — it is never imported into `unseen_university/`, only shelled
to. Keep it isolated:

```powershell
py -m venv $env:USERPROFILE\.aider-venv
& $env:USERPROFILE\.aider-venv\Scripts\python.exe -m pip install --upgrade pip
& $env:USERPROFILE\.aider-venv\Scripts\pip.exe install aider-chat
# sanity: this must print a version
& $env:USERPROFILE\.aider-venv\Scripts\aider.exe --version
```

`consts.aider_bin()` will find `%USERPROFILE%\.aider-venv\Scripts\aider.exe` automatically. If you
put aider elsewhere, set `$env:AIDER_BIN` to the full path of `aider.exe`.

### 2. A Python venv to RUN the runner from (pytest is the only real dep)

You do **not** need to `pip install -e .`. The runner is stdlib-only and imports through empty,
lazy package `__init__`s (verified: importing it pulls **zero** heavy deps — no psycopg2, no bus).
You only need `pytest` (the gate runs `python -m pytest` inside the clone):

```powershell
py -m venv C:\src\uu-runner-venv
& C:\src\uu-runner-venv\Scripts\pip.exe install pytest
```

### 3. Point at Hex and the repo

```powershell
$env:PYTHONPATH   = "C:\src\UnseenUniversity"      # so `python -m unseen_university...` resolves
$env:HEX_OLLAMA   = "http://10.0.0.100:11434"      # or wherever Hex is
# AIDER_BIN only if aider isn't at %USERPROFILE%\.aider-venv\Scripts\aider.exe
```

### 4. Prove aider + Hex + the runner end-to-end (self-contained, no creds)

Make a tiny throwaway repo with a failing test, then run the runner against it. This exercises the
whole path — clone → branch → aider on Hex → gate → commit — with clean import resolution (the
target is self-contained, so it's a *real* green):

```powershell
$SB = "$env:TEMP\aider_sb_src"
Remove-Item -Recurse -Force $SB -ErrorAction Ignore
New-Item -ItemType Directory -Force "$SB\shop" | Out-Null
Set-Content "$SB\shop\__init__.py" ""
Set-Content "$SB\shop\models.py" @'
class Order:
    def __init__(self, items):
        self.items = items
    def total(self):
        """sum of unit_price * qty over all items"""
        raise NotImplementedError
'@
Set-Content "$SB\test_models.py" @'
from shop.models import Order
def test_total():
    assert Order([("w",10.0,2),("g",5.0,3)]).total() == 35.0
'@
Push-Location $SB; git init -q; git config user.email t@local; git config user.name t; git add -A; git commit -qm RED; Pop-Location

& C:\src\uu-runner-venv\Scripts\python.exe -m unseen_university.devices.aider.runner `
  --repo $SB --ticket win-smoke `
  --message "Implement Order.total() to return sum of unit_price*qty over self.items. Do not edit tests. Then stop." `
  --test test_models.py --affected shop/models.py --model qwen3-coder:30b
```

**Expected:** a block ending in `[GATE] PASS`, with `edited=True`, `tests green=True`, a wall time
of roughly 15–40s, and exit code 0. If you get that, **the aider builder runs on Windows.** That's
the milestone.

---

## Env knobs (all optional; sane defaults in `consts.py`)

| Var | Default | What |
|---|---|---|
| `HEX_OLLAMA` / `OLLAMA_API_BASE` | `http://10.0.0.100:11434` | ollama endpoint the runner exports to aider |
| `AIDER_BIN` | `~/.aider-venv/Scripts/aider.exe` (auto) | aider executable, if not in the default venv |
| `AIDER_VENV` | `~/.aider-venv` | where `aider_bin()` looks for `Scripts\aider.exe` |
| `AIDER_DEVICE_MODEL` | `qwen3-coder:30b` | build model (MoE, ~2.2× faster than devstral, equally correct) |
| `AIDER_DEVICE_FALLBACK_MODEL` | `devstral-small-2:24b` | dense fallback |
| `AIDER_WORKROOT` | `~/.unseen_university/aider_work` | where throwaway clones land |
| `AIDER_REPO_SOURCE` | the UU repo | repo the device clones per ticket (device layer only) |

Runner CLI: `--repo --message --ticket --model --test (repeatable) --file (repeatable, pre-adds to
aider chat) --affected (repeatable, advisory scope) --map-tokens --timeout`. `--test` omitted ⇒ the
gate can't assert correctness and reports FAIL (by design — no silent green).

---

## Gotchas (read before you trust a result)

- **The editable-install finder beats PYTHONPATH — do NOT validate `unseen_university`-package
  tickets inside a clone yet.** If UU is `pip install -e .` anywhere on this box, a MetaPathFinder in
  site-packages resolves `unseen_university.*` to the *original* tree, so tests run in a clone import
  un-edited code. Mostly the safe direction (normal tickets escalate rather than false-close), but
  don't rely on an in-clone green for a UU-package edit. Self-contained targets (like the smoke's
  `shop/`) are fine. Full fix is ticketed: **T-aider-real-uu-ticket-proof** (clone-test isolation).
  On *this* box you likely won't `pip install -e .` at all (see bring-up), which sidesteps it — but
  know the trap.
- **The gate is objective, not vibes.** `[GATE] PASS` means aider edited files, the `--test` target
  is green, and the diff didn't touch tests / `.github/` / escape the repo. A `FAIL` with
  `edited=True tests_green=False` means aider tried but the code is wrong → that's an *escalate*, not
  a bug in the runner.
- **aider drops `.aider*` scratch files** into the repo; the runner filters them out of the
  changed-file set and git-excludes them in the clone. If you see them leak, that filter regressed.
- **map-tokens on a big repo is slow.** For a multi-file task where aider must find files itself, the
  repo map (`--map-tokens 1024`) does the orienting — great on small repos, unproven at UU's 613-file
  scale. For a first real run, pass the target files with `--file` to skip discovery.

---

## What NOT to do yet

- Don't wire Granny / the bus / Postgres. The runner needs none of it.
- Don't `pip install -e .` unless you actually need the full package (you don't, for the runner).
- Don't tag real `unseen_university`-package tickets `Aider` for dispatch until
  T-aider-real-uu-ticket-proof lands (clone-test isolation).

---

## Creds

**The runner milestone needs zero credentials** — Hex is local $0 inference (no API key), and the
runner touches no vault, no database, no remote. If you later stand up the *rack device* (bus
dispatch), you'll need the Postgres boot secret + vault access — **ask Akien; they are never in
source** (`.env` is gitignored; secrets live in the vault device). But that's layer 2. Get the
runner green first.

---

## Pointers (where to read next)

- `unseen_university/devices/aider/runner.py` — the thing you're running (has a full module docstring).
- `unseen_university/devices/aider/consts.py` — every env knob + the `aider_bin()` Windows resolver.
- `unseen_university/devices/aider/device.py` / `shim.py` / `worker_listener.py` — the rack layer (layer 2).
- `devlab/claudecode/aider_smoke.py` — the ORIGINAL proof harness (Linux-pathed: its `AIDER_BIN`
  hardcodes `bin/aider`, so it won't find `Scripts\aider.exe` — use the runner, not this, on Windows).
- Memory: `project_aider_builder_viable` (why aider is the answer), `reference_editable_finder_beats_pythonpath` (the clone trap).
- Tickets: `T-aider-real-uu-ticket-proof`, `T-aider-swarm-deployment`, `T-builder-merge-time-proof`
  (`python3 devlab/claudecode/cc_queue.py show <id>`).

Good luck. When the smoke prints `[GATE] PASS`, tell Akien — that's aider building on Windows.
