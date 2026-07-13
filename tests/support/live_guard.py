"""Capability guard for live-inference tests.

WHY A BARE TCP CONNECT IS NOT A GUARD.

The live smoke test used to gate itself on this:

    with socket.create_connection((HEX_HOST, HEX_PORT), timeout=3):
        return True

**A TCP connect succeeds against a service whose queue is an hour deep.** On 2026-07-13 that
guard admitted a live 24B multi-turn build onto a Hex whose single inference slot (`-np 1`) was
already saturated by our own orphaned test runs. The run took hours instead of minutes, never
exited, never released its socket — and the next run piled on top. Nine of them accumulated, and
the resulting queue got diagnosed (twice, wrongly) as "Hex is broken."

**REACHABILITY IS EXACTLY THE PROPERTY THAT STAYS TRUE WHILE CAPACITY IS ZERO.** A port that
answers, a `/api/tags` that returns 200 — neither says the service can do the job. The only probe
that distinguishes a working host from a hopeless one is *asking it to do the job*, small and fast,
and treating slow as no.

So this guard performs a real one-token completion with a short timeout. It answers the question the
test actually needs answered — "can I get inference out of this box RIGHT NOW?" — rather than the
one that is merely easy to ask.

This costs one tiny request, once per session, at collection time. That is affordable precisely
because it is not on a timer: a *periodic* capability probe would add load to the very slot whose
scarcity it is measuring (see T-default-suite-drives-live-inference-and-saturates-hex).

See tests/inference/test_live_tests_are_opt_in.py for the proof.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

# Slow IS down, for our purposes. A host that needs more than this to emit ONE token is a host
# whose queue we must not join — joining it is what built the pile-up in the first place.
DEFAULT_PROBE_TIMEOUT = 20.0


def can_infer(endpoint: str, model: str, timeout: float = DEFAULT_PROBE_TIMEOUT) -> bool:
    """Can this endpoint actually COMPLETE, right now, within `timeout`?

    True only if a real one-token completion comes back. A refused connection, a timeout, an HTTP
    error, malformed JSON, or an empty answer all mean NO — because from the caller's side they are
    the same thing: no inference is available here.
    """
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
        "options": {"num_predict": 1},
    }).encode()
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        # Includes socket.timeout — a saturated host times out here, and that is the whole point.
        return False
    return bool(body.get("message", {}).get("content", "").strip())
