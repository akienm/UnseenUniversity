#!/usr/bin/env python3
"""aider_smoke.py — headless aider control-arm run against Hex on a self-contained task.

The seed of the aider-builder runner (T-aider-builder-first-run). aider is a KNOWN
QUANTITY — proven to make small local models edit. This proves it does so against OUR
inference (Hex ollama, $0) on a deterministic capability task (implement a TTLCache so a
failing pytest goes green), in an ISOLATED sandbox (own git, no UU remote — blast radius
contained by construction).

aider is an EXTERNAL DEPENDENCY: installed in its own venv (~/.aider-venv), invoked by
subprocess only, never imported into unseen_university/.

Usage:
  python3 devlab/claudecode/aider_smoke.py --model devstral-small-2:24b
  python3 devlab/claudecode/aider_smoke.py --model qwen3-coder:30b
  python3 devlab/claudecode/aider_smoke.py --model devstral-small-2:24b --keep   # keep sandbox

Each run recreates the sandbox pristine (RED), fires aider once headless, then runs pytest
and reports GREEN/RED + wall-clock. Re-runnable and model-parameterized so the 2x2 can reuse it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

AIDER_VENV = Path.home() / ".aider-venv"
AIDER_BIN = AIDER_VENV / "bin" / "aider"
SANDBOX = Path.home() / "aider-sandbox"
HEX_OLLAMA = "http://10.0.0.100:11434"

# Task packs. Each is {files: {name: content}, message: task, chat_files: [names to add]}.
# "ttl": a 2-file toy (single-file fix). "checkout": a multi-file coordinated change requiring
# orientation across 3 modules (the failure mode DS died on — read-wandering, not serialization).
TASKS = {
    "ttl": {
        "chat_files": ["ttl_cache.py", "test_ttl_cache.py"],
        "map_tokens": "0",  # 2-file toy: no repo map needed
        "files": {
            "ttl_cache.py": '''\
"""A cache whose entries expire after ttl_seconds."""


class TTLCache:
    def __init__(self, ttl_seconds, time_fn=None):
        """ttl_seconds: entry lifetime. time_fn: clock (default time.monotonic), injectable for tests."""
        raise NotImplementedError

    def set(self, key, value):
        raise NotImplementedError

    def get(self, key):
        """Return the value for key if present AND not expired, else None."""
        raise NotImplementedError
''',
            "test_ttl_cache.py": '''\
from ttl_cache import TTLCache


def test_set_get():
    c = TTLCache(ttl_seconds=10, time_fn=lambda: 0)
    c.set("a", 1)
    assert c.get("a") == 1


def test_missing_returns_none():
    c = TTLCache(ttl_seconds=10, time_fn=lambda: 0)
    assert c.get("missing") is None


def test_expiry():
    now = {"t": 0}
    c = TTLCache(ttl_seconds=10, time_fn=lambda: now["t"])
    c.set("a", 1)
    now["t"] = 5
    assert c.get("a") == 1      # not yet expired
    now["t"] = 11
    assert c.get("a") is None   # expired


def test_overwrite_refreshes_ttl():
    now = {"t": 0}
    c = TTLCache(ttl_seconds=10, time_fn=lambda: now["t"])
    c.set("a", 1)
    now["t"] = 8
    c.set("a", 2)               # refresh at t=8 -> expires at 18
    now["t"] = 15
    assert c.get("a") == 2
''',
        },
        "message": (
            "Implement the TTLCache class in ttl_cache.py so that all tests in test_ttl_cache.py pass. "
            "Do NOT edit the tests. get() must return the stored value only if it was set within the last "
            "ttl_seconds according to time_fn; otherwise return None. set() records the value with the current "
            "time_fn timestamp. If time_fn is None, default it to time.monotonic. Then stop."
        ),
    },
    # Multi-file: passing requires an Order.total() in models.py AND wiring pricing.apply_discount
    # into service.checkout() — a coordinated 2-file edit while reading a 3rd (pricing.py). The repo
    # map is ON so aider orients across modules instead of read-wandering.
    "checkout": {
        "chat_files": [],  # NOTHING pre-added — aider must FIND the files via the repo map (the real test)
        "map_tokens": "1024",
        "files": {
            "shop/__init__.py": "",
            "shop/models.py": '''\
"""Domain models for the shop."""


class Order:
    def __init__(self, items):
        # items: list of (name, unit_price, qty)
        self.items = items

    def total(self):
        """Return the pre-discount total: sum of unit_price * qty over all items."""
        raise NotImplementedError
''',
            "shop/pricing.py": '''\
"""Discount codes. (Already implemented — read this, do not change it.)"""

_CODES = {"SAVE10": 0.10, "SAVE25": 0.25}


def apply_discount(total, code):
    """Return total reduced by the code's fraction; unknown/None code returns total unchanged."""
    frac = _CODES.get(code, 0.0)
    return round(total * (1.0 - frac), 2)
''',
            "shop/service.py": '''\
"""Checkout service."""

from shop.models import Order
from shop.pricing import apply_discount


def checkout(order, code=None):
    """Return the final price for `order` after applying discount `code`.

    Must use Order.total() for the pre-discount sum and pricing.apply_discount for the code.
    """
    raise NotImplementedError
''',
            "test_checkout.py": '''\
from shop.models import Order
from shop.service import checkout


def _order():
    return Order([("widget", 10.0, 2), ("gadget", 5.0, 3)])   # 20 + 15 = 35.0


def test_total():
    assert _order().total() == 35.0


def test_checkout_no_code():
    assert checkout(_order()) == 35.0


def test_checkout_save10():
    assert checkout(_order(), "SAVE10") == 31.5      # 35 * 0.90


def test_checkout_save25():
    assert checkout(_order(), "SAVE25") == 26.25     # 35 * 0.75


def test_checkout_unknown_code():
    assert checkout(_order(), "BOGUS") == 35.0
''',
        },
        "message": (
            "Make all tests in test_checkout.py pass. Do NOT edit the tests or shop/pricing.py. "
            "You will need to: (1) implement Order.total() in shop/models.py to sum unit_price*qty over "
            "self.items; (2) implement checkout() in shop/service.py to compute order.total() then apply "
            "the discount code via apply_discount. Then stop."
        ),
    },
}


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _pytest_green(sandbox: Path) -> tuple[bool, str]:
    r = _run([sys.executable, "-m", "pytest", "-q", "--tb=line", str(sandbox)], cwd=sandbox)
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0, out.splitlines()[-1] if out else "(no output)"


def build_sandbox(task: dict) -> Path:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    for rel, content in task["files"].items():
        p = SANDBOX / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    # Own git repo, no remote -> aider (git-native) works; cannot touch the UU repo.
    _run(["git", "init", "-q"], cwd=SANDBOX)
    _run(["git", "config", "user.email", "aider-smoke@local"], cwd=SANDBOX)
    _run(["git", "config", "user.name", "aider-smoke"], cwd=SANDBOX)
    _run(["git", "add", "-A"], cwd=SANDBOX)
    _run(["git", "commit", "-qm", "sandbox: RED (stubs + failing tests)"], cwd=SANDBOX)
    return SANDBOX


def run_aider(model: str, task: dict) -> float:
    env = dict(os.environ)
    env["OLLAMA_API_BASE"] = HEX_OLLAMA
    cmd = [
        str(AIDER_BIN),
        *task["chat_files"],                 # files pre-added to chat (empty => aider finds them via repo map)
        "--model", f"ollama_chat/{model}",
        "--message", task["message"],
        "--yes-always",
        "--no-auto-commits",                 # keep edits uncommitted so we inspect raw diffs
        "--no-check-update",
        "--no-analytics",
        "--no-gitignore",
        "--map-tokens", task["map_tokens"],  # repo map budget (0 = off for the toy)
    ]
    t0 = time.monotonic()
    r = _run(cmd, cwd=SANDBOX, env=env, timeout=900)
    dt = time.monotonic() - t0
    (SANDBOX / "_aider_stdout.txt").write_text(r.stdout or "")
    (SANDBOX / "_aider_stderr.txt").write_text(r.stderr or "")
    return dt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="devstral-small-2:24b",
                    help="Hex ollama model (devstral-small-2:24b | qwen3-coder:30b)")
    ap.add_argument("--task", default="ttl", choices=sorted(TASKS),
                    help="task pack: ttl (2-file toy) | checkout (multi-file orientation)")
    ap.add_argument("--keep", action="store_true", help="keep the sandbox after the run")
    args = ap.parse_args()

    if not AIDER_BIN.exists():
        sys.exit(f"ERROR: aider not installed at {AIDER_BIN} — run: "
                 f"python3 -m venv {AIDER_VENV} && {AIDER_VENV}/bin/pip install -e /home/akien/dev/src/aider")

    task = TASKS[args.task]
    print(f"== aider smoke :: task={args.task} :: model=ollama_chat/{args.model} :: Hex={HEX_OLLAMA} ==")
    build_sandbox(task)
    red_green, red_line = _pytest_green(SANDBOX)
    print(f"[pre]  tests green={red_green}  ({red_line})")
    if red_green:
        sys.exit("ERROR: sandbox is not RED before aider — the task is not a valid red->green proof")

    print("[run]  firing aider headless (up to 900s)...")
    try:
        dt = run_aider(args.model, task)
    except subprocess.TimeoutExpired:
        print("[run]  aider TIMED OUT (>900s)")
        dt = 900.0

    edited = _run(["git", "diff", "--stat"], cwd=SANDBOX).stdout.strip()
    post_green, post_line = _pytest_green(SANDBOX)
    print(f"[post] wall={dt:.1f}s  edited_files={'yes' if edited else 'NO'}  tests_green={post_green}")
    print(f"[post] {post_line}")
    if edited:
        print("[diff]\n" + edited)
    verdict = "GREEN (aider completed the task)" if post_green else (
        "EDITED-BUT-RED (aider edited, tests still fail)" if edited else "NO-EDIT (aider produced 0 edits)")
    print(f"== VERDICT [{args.task}/{args.model}]: {verdict} ==")

    if not args.keep and post_green:
        shutil.rmtree(SANDBOX)
    else:
        print(f"(sandbox kept at {SANDBOX} — see _aider_stdout.txt / git diff)")


if __name__ == "__main__":
    main()
