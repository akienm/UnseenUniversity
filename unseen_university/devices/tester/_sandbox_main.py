"""The process that runs INSIDE the sandbox. Not for direct use.

Order matters and is the whole design:
  1. configure our own netns (we are root in the user namespace, so we may)
  2. claim the addresses the policy names, serve the fixtures, arm the refusers
  3. VERIFY the policy from inside — every rule, every run. Never assert what you can measure.
  4. run pytest
  5. hand the connection record back out

Step 3 is not ceremony. A sandbox whose network policy silently failed to apply looks EXACTLY like
one that worked, right up until a test reaches the real Hex — and then it looks like a broken
server, which is precisely how three wrong diagnoses got made on 2026-07-13. The seal is measured.

Step 5 is the other half. A router does not sample connections, it IS them, so "what did this test
reach for" stops being something we infer from `ss` and becomes something we know.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys

from unseen_university.devices.tester.netpolicy import (
    DENY,
    FIXTURE,
    FORWARD,
    REFUSE,
    NetworkPolicy,
    Router,
)


def _observe(host: str, port: int, timeout: float = 3.0) -> str:
    """What does this address ACTUALLY do, right now, from in here?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "connected"
    except ConnectionRefusedError:
        return "refused"
    except OSError as exc:
        # ENETUNREACH / EHOSTUNREACH — there is no route at all.
        return "unreachable" if exc.errno in (101, 113) else f"error:{exc.errno}"


_EXPECTED = {FIXTURE: "connected", FORWARD: "connected", REFUSE: "refused", DENY: "unreachable"}


def verify(policy: NetworkPolicy) -> list[dict]:
    """Does the network actually behave the way the policy says? Ask it."""
    out = []
    for rule in policy.rules:
        observed = _observe(rule.host, rule.port)
        expected = _EXPECTED[rule.action]
        out.append({
            "host": rule.host, "port": rule.port, "action": rule.action,
            "expected": expected, "observed": observed, "holds": observed == expected,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()

    policy = NetworkPolicy.from_json(args.policy)
    router = Router(policy)
    report = {"policy_holds": False, "checks": [], "attempts": [], "returncode": None}

    try:
        router.bring_up()
    except Exception as exc:
        report["checks"] = [{"error": f"could not build the network: {exc}"}]
        _write(args.report, report)
        return 90   # the SANDBOX failed, not the tests. Never a pass.

    report["checks"] = verify(policy)
    report["policy_holds"] = all(c.get("holds") for c in report["checks"])

    if not report["policy_holds"]:
        # Refuse to run tests through a network we cannot vouch for. A green from an unverified
        # sandbox is worth nothing, and worse, it LOOKS like something.
        _write(args.report, report)
        router.shutdown()
        return 91

    cmd = [c for c in args.cmd if c != "--"]
    proc = subprocess.run(cmd, cwd=os.getcwd())
    report["returncode"] = proc.returncode
    report["attempts"] = router.attempts
    router.shutdown()
    _write(args.report, report)
    return proc.returncode


def _write(path: str, report: dict) -> None:
    with open(path, "w") as fh:
        json.dump(report, fh)


if __name__ == "__main__":
    sys.exit(main())
