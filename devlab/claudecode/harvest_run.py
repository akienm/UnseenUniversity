#!/usr/bin/env python3
"""harvest_run.py — fire one harvest-mode coding run and report the harvested data.

The harvest machine (T-ds-harvest-mode-escalation-off + T-ds-stuck-ladder-and-rung-log +
T-ds-harvest-mode-operator-toggle + T-ds-domain-cwd-isolation) is complete; this runs it once
against Hex to GENERATE the corpus the classifier/resume tickets need (Akien 2026-07-05).

Safety (CP6): the coding domain is edit-capable (aci_mode: bash/edit/write). The run is pinned
to an ISOLATED scratch dir via the cwd seam — never the live repo. Routing is confirmed to be
the free Hex ollama tier before the run is trusted as a real capability wall (a paid OpenRouter
fall-through would be a routing artifact, not a harvest signal).

Observation proof (not red→green — advisor): a completed run leaves >=1 io_corpus record with
provider=ollama AND >=1 rung-choice record in inference_starve for the run's ticket.

Usage:
    python3 devlab/claudecode/harvest_run.py [--endpoint URL] [--ticket-id ID] [--keep-workdir]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# A deliberately hard, underspecified coding ask: large enough that a fixed worker-tier model
# (devstral-24b) plausibly walls within the turn cap rather than reaching DONE — the wall is the
# harvest signal. If it DOES finish, that is also valid data (a rare warm-ish win).
_SYNTHETIC_TICKET = {
    "id": "T-harvest-synthetic-001",
    "title": "Implement a Raft consensus module with leader election + log replication + tests",
    "tags": ["Distributed", "Consensus"],
    "description": (
        "In this repo, implement a working Raft consensus library: leader election, log "
        "replication, term/vote persistence, and a simulated multi-node test harness proving "
        "a committed entry survives a leader failure. Include pytest tests that pass."
    ),
}


def _warm_model(endpoint: str, model: str = "devstral-small-2:24b") -> None:
    """Load the model into Hex's KV cache before the run.

    The first cold call to a 24B model includes weight-load + first-token latency that blew past
    the 120s usage_based dispatch timeout on the first harvest run (2026-07-05) — the loop then
    classified the timeout as AVAILABILITY (not capability) and escaped the harvest ladder. Warming
    turns the real turns fast enough to fit the timeout, so the wall that gets harvested is a real
    capability wall, not a cold-start artifact. keep_alive holds the model resident for the run.
    """
    import json as _json
    import urllib.request

    body = _json.dumps({
        "model": model, "prompt": "ready?", "stream": False, "keep_alive": "20m",
    }).encode()
    req = urllib.request.Request(f"{endpoint}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    print(f"warming {model} on {endpoint} (loading weights into KV cache; may take ~1–2 min cold)…")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            resp.read()
        print("warm-up complete — model resident.")
    except Exception as exc:  # noqa: BLE001 — warm-up is best-effort; the run still reports honestly
        print(f"warm-up failed (non-fatal, run may still cold-start): {exc}")


def _pin_low_turn_cap(max_turns: int) -> None:
    """Force the coding loop to wall at a low turn cap so a REAL capability wall lands fast.

    The natural cap is 50 (usage_based) / 80 (flat-rate) — at ~4 min/turn on a fixed local tier
    that's ~5 hours to LOOP_MAX_TURNS, and the whole crawl is exposed to the growing-context 120s
    dispatch cliff that would escape the harvest ladder as AVAILABILITY (b62xia6sh/b7d5uqwbc did
    exactly this). A turn-exhaustion wall routes through the identical capability→StuckLadder→rung
    path as a real can't-finish (agentic_loop LOOP_MAX_TURNS → _classify 'capability'), so pinning
    the cap low yields the SAME proof in ~20–40 min with a real devstral wall, real corpus records,
    real routing. Contained to this operator harness — production caps are untouched. Both the
    architect and editor sub-loops read the module-level AgenticLoop via architect_editor, so
    patching that one reference (with both caps pinned, defeating the flat-rate bump) covers both.
    """
    import functools

    from unseen_university.devices.inference.domains import architect_editor

    architect_editor.AgenticLoop = functools.partial(
        architect_editor.AgenticLoop, max_turns=max_turns, flat_rate_max_turns=max_turns,
    )
    print(f"pinned coding turn cap to {max_turns} (both loops) — forcing a fast capability wall")


def _make_scratch(keep: bool) -> Path:
    """An isolated, git-initialized scratch dir for the edit-capable model to work in."""
    d = Path(tempfile.mkdtemp(prefix="uu_harvest_"))
    subprocess.run(["git", "init", "-q"], cwd=d, check=False)
    (d / "README.md").write_text("# harvest scratch — throwaway\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=d, check=False)
    subprocess.run(["git", "-c", "user.email=h@h", "-c", "user.name=h", "commit", "-qm", "seed"],
                   cwd=d, check=False)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://hex.local:11434", help="ollama/Hex base URL")
    ap.add_argument("--ticket-id", default="", help="a real ticket id (default: synthetic wall-seeker)")
    ap.add_argument("--keep-workdir", action="store_true", help="leave the scratch dir for inspection")
    ap.add_argument("--max-turns", type=int, default=4,
                    help="pin the coding loop's turn cap low so a real capability wall lands fast "
                         "(0 = leave the production 50/80 caps and let it run to natural exhaustion)")
    args = ap.parse_args()

    # Env MUST be set before the InferenceDevice registers its ollama source (endpoint) and before
    # any domain is resolved (harvest toggle). The loop constructs the device lazily on .run().
    os.environ["INFERENCE_ENDPOINT"] = args.endpoint
    os.environ["UU_HARVEST_MODE"] = "1"

    from unseen_university.devices.inference.domains import resolve_domain
    from unseen_university.devices.inference.domains.stuck_ladder import read_rung_choices
    from unseen_university.devices.inference import io_corpus

    # Pre-flight: confirm the coding route resolves to the FREE Hex ollama tier, not paid OpenRouter.
    from unseen_university.devices.inference.device import InferenceDevice
    dev = InferenceDevice()
    dom = resolve_domain("coding")
    if not dom.harvest_mode:
        print("ABORT: UU_HARVEST_MODE did not take — resolved domain is not in harvest mode.")
        return 2
    dec = dom.select(dev._rules, task_class="worker", required_difficulty="code")
    route_src = getattr(getattr(dec, "source", None), "name", "?")
    route_cost = getattr(getattr(dec, "model", None), "input_cost_per_1m", None)
    print(f"pre-flight route: source={route_src} cost_per_1m={route_cost} endpoint={args.endpoint}")
    if route_src != "ollama":
        print(f"ABORT: coding route is '{route_src}', not free Hex ollama — a run now would be a "
              f"routing artifact (or paid). Bring Hex up / check INFERENCE_ENDPOINT.")
        return 3

    _warm_model(args.endpoint)

    if args.max_turns > 0:
        _pin_low_turn_cap(args.max_turns)

    ticket = dict(_SYNTHETIC_TICKET)
    if args.ticket_id:
        import subprocess as sp
        raw = sp.run(["python3", "devlab/claudecode/cc_queue.py", "show", args.ticket_id],
                     capture_output=True, text=True)
        try:
            ticket = json.loads(raw.stdout)
        except Exception:
            print(f"could not load ticket {args.ticket_id}; using synthetic")
            ticket = dict(_SYNTHETIC_TICKET)

    scratch = _make_scratch(args.keep_workdir)
    ticket_id = ticket.get("id", "?")
    started = datetime.now(timezone.utc).isoformat()
    print(f"\n=== HARVEST RUN ticket={ticket_id} cwd={scratch} started={started} ===")

    try:
        result = resolve_domain("coding").run(ticket, cwd=scratch, agent_id="harvest.0")
        print(f"\nLoopResult (domain.run return): {str(result)[:300]!r}")
    except Exception as exc:  # noqa: BLE001 — a harvest run must report, not crash the operator
        print(f"\nrun raised (harvested as failure): {type(exc).__name__}: {exc}")
        result = None
    finally:
        if not args.keep_workdir:
            subprocess.run(["rm", "-rf", str(scratch)], check=False)

    # ── Observation proof: corpus (provider=ollama) + a rung-choice record ──────────────
    print("\n=== HARVESTED DATA ===")
    rungs = read_rung_choices(ticket_id=ticket_id)
    print(f"rung-choice records for {ticket_id}: {len(rungs)}")
    for r in rungs[-3:]:
        print(f"  rung={r.get('rung')} tier={r.get('tier')} turn={r.get('turn_reached')} reason={r.get('reason')}")

    corpus_hits = []
    for f in sorted(glob.glob(str(io_corpus.corpus_root() / "*.io.jsonl"))):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("ts", "") >= started:
                corpus_hits.append(rec)
    providers = {}
    for rec in corpus_hits:
        p = rec.get("provider", "?")
        providers[p] = providers.get(p, 0) + 1
    print(f"io_corpus records since run start: {len(corpus_hits)}  providers={providers}")
    for rec in corpus_hits[:3]:
        print(f"  provider={rec.get('provider')} model={rec.get('model')} outcome={rec.get('outcome')} "
              f"role={rec.get('role')} turn={rec.get('turn')} $={rec.get('dollars', rec.get('cost'))}")

    ok_corpus = any(r.get("provider") == "ollama" for r in corpus_hits)
    ok_rung = len(rungs) >= 1
    print(f"\nOBSERVATION: corpus_has_ollama={ok_corpus} rung_recorded={ok_rung} "
          f"-> aperture {'OPEN' if (ok_corpus and ok_rung) else 'NOT-yet-open'}")
    return 0 if (ok_corpus and ok_rung) else 1


if __name__ == "__main__":
    sys.exit(main())
