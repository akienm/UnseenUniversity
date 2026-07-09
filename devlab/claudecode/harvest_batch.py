#!/usr/bin/env python3
"""harvest_batch.py — batch-harvest a distinction-bearing stuck corpus + seal an eval slice.

Runs harvest-mode over a CURATED seed set spanning the two wall classes the defeating-question
classifier exists to separate:

  * DESIGN-STUCK    — the spec didn't say; the builder flails for lack of intent (the gold —
                      a sortable question). Induced by a deliberately UNDER-specified ask.
  * CAPABILITY-STUCK — well-specified, but the fixed tier simply can't. Induced by a
                      WELL-specified-but-hard ask.

At a REALISTIC turn cap (not 4 — turn-starvation blurs both classes into "hit the cap"), each
transcript CONTAINS its distinction: a design-stuck run wanders because it can't tell WHAT to
build; a capability-stuck run understands the task and produces broken code. Real transcripts
land in io_corpus by ticket_id (role/turn per T-corpus-visibility-gaps); a held-out subset is
sealed via eval_slice.py as the reality-uncoupled eval surface the classifier grades against —
before the classifier exists, so the temporal firewall is maximal (Akien 2026-07-05).

Seed schema (devlab/claudecode/harvest_seeds/*.json):
    {"id": "T-harvest-...", "class_intent": "design_stuck"|"capability_stuck",
     "split": "eval"|"dev", "title": str, "tags": [str], "description": str,
     "scratch_files": {"<relpath>": "<content>"}}
``scratch_files`` seeds a tiny toy codebase so a design-stuck ask lands in a REAL (if small)
context — an under-specified ask in an EMPTY repo is context-empty, not design-stuck. The
``split`` marks which seeds' transcripts get sealed into the held-out eval slice vs. left
dev-visible. ``class_intent`` is the SEED-DESIGN intent, NOT the ground-truth label — the label
of record comes from Akien's human pass over the real transcripts (T-ds-defeating-question-classifier).

Safety (CP6): each seed runs in its own isolated git scratch dir (the cwd seam), route-guarded to
free Hex ollama ($0) before the batch is trusted as real harvest data (a paid/mis-routed run is a
routing artifact, not a wall). No production inference code changes — the harvest machine is built.

Usage:
    python3 devlab/claudecode/harvest_batch.py [--endpoint URL] [--seeds DIR] [--max-turns N]
                                               [--slice-name NAME] [--keep-workdir]
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

# Reuse the single-run operator's proven helpers — one home for warm/pin, no duplication.
from harvest_run import _pin_low_turn_cap, _warm_model  # type: ignore

log = __import__("logging").getLogger(__name__)

_DEFAULT_SEEDS_DIR = Path(__file__).resolve().parent / "harvest_seeds"


def _load_seeds(seeds_dir: Path) -> list[dict]:
    """Load every *.json seed in seeds_dir, sorted by id for a deterministic batch order."""
    seeds = []
    for f in sorted(glob.glob(str(seeds_dir / "*.json"))):
        seeds.append(json.loads(Path(f).read_text(encoding="utf-8")))
    return sorted(seeds, key=lambda s: s.get("id", ""))


def _make_seeded_scratch(seed: dict, keep: bool) -> Path:
    """An isolated git scratch dir seeded with the seed's toy codebase (or a bare README).

    Used ONLY in the hermetic plumbing test / no-checkout fallback. NOT the real harvest path:
    the first batch proved devstral ignores a tmp scratch and hard-orients on the live repo via
    absolute paths. The real path is _reset_checkout (checkout-mode) — a full throwaway UU tree the
    model orients on authentically, run AS dicksimnel so the live tree is permission-unreachable.
    """
    d = Path(tempfile.mkdtemp(prefix=f"uu_harvest_{seed.get('id','seed')}_"))
    subprocess.run(["git", "init", "-q"], cwd=d, check=False)
    files = seed.get("scratch_files") or {"README.md": "# harvest scratch — throwaway\n"}
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=d, check=False)
    subprocess.run(["git", "-c", "user.email=h@h", "-c", "user.name=h", "commit", "-qm", "seed"],
                   cwd=d, check=False)
    return d


def _reset_checkout(checkout: Path) -> Path:
    """Reset the throwaway sandbox checkout to a pristine tree, ready for the next seed.

    The sandbox is a plain git clone under ~/dicksimnel (a DIRECTORY, not a system user — Akien's
    call, 2026-07-06). The batch runs as akien with HOME pointed at the sandbox home, so devstral's
    `cd ~/dev/src/UnseenUniversity` lands HERE (authentic full-repo orientation). Absolute escapes
    to the live tree are rejected by the tool-layer guard (_install_sandbox_guard). git reset --hard
    + clean -fdx wipes the prior seed's edits so each run starts pristine.
    """
    subprocess.run(["git", "-C", str(checkout), "reset", "--hard", "-q"], check=False)
    subprocess.run(["git", "-C", str(checkout), "clean", "-fdxq"], check=False)
    log.info("harvest_batch: reset checkout %s to pristine HEAD", checkout)
    return checkout


def _ensure_throwaway_clone(checkout: Path, source_repo: Path) -> Path:
    """Ensure a throwaway UU clone exists at `checkout`; return it.

    A plain local clone (as akien — no user, no sudo). Independent of the live tree: devstral's edits
    and per-seed resets happen here only. With the coding prompt now using RELATIVE paths (no
    hardcoded ~/dev/src/UnseenUniversity), cwd=this-clone keeps devstral inside it — the source fix,
    not a guard. Created once; reused (reset pristine) across seeds.
    """
    if (checkout / ".git").is_dir():
        return checkout
    checkout.parent.mkdir(parents=True, exist_ok=True)
    # -c safe.directory='*' so cloning our own repo never trips git's ownership guard.
    subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q", str(source_repo), str(checkout)],
                   check=False)
    log.info("harvest_batch: created throwaway clone %s (from %s)", checkout, source_repo)
    return checkout


def _corpus_records_since(since_ts: str) -> list[dict]:
    """Every io_corpus record with ts >= since_ts, across today's corpus files."""
    from unseen_university.devices.inference import io_corpus

    out = []
    for f in sorted(glob.glob(str(io_corpus.corpus_root() / "*.io.jsonl"))):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("ts", "") >= since_ts:
                out.append(rec)
    return out


def _run_seed(seed: dict, *, keep: bool, checkout: Path | None = None) -> dict:
    """Run one seed through harvest-mode. Returns a per-run summary.

    checkout-mode (real path): cwd is the dedicated throwaway checkout, reset pristine before the
    run — devstral orients on a full UU tree it can safely edit. No-checkout (test/fallback): a tmp
    scratch. The transcript is captured by io_corpus as a side effect (correlated by ticket_id).
    """
    from unseen_university.devices.inference.domains import resolve_domain
    from unseen_university.devices.inference.domains.stuck_ladder import read_rung_choices

    ticket = {
        "id": seed["id"], "title": seed.get("title", ""),
        "tags": seed.get("tags", []), "description": seed.get("description", ""),
    }
    cwd = _reset_checkout(checkout) if checkout is not None else _make_seeded_scratch(seed, keep)
    started = datetime.now(timezone.utc).isoformat()
    log.info("harvest_batch: run seed=%s class_intent=%s split=%s cwd=%s",
             seed["id"], seed.get("class_intent"), seed.get("split"), cwd)
    result = None
    try:
        result = resolve_domain("coding").run(ticket, cwd=cwd, agent_id="harvest.batch")
    except Exception as exc:  # noqa: BLE001 — a batch must report, never crash on one seed
        log.warning("harvest_batch: seed=%s raised (harvested as failure): %s: %s",
                    seed["id"], type(exc).__name__, exc)
    finally:
        if checkout is None and not keep:  # checkout is reset (not deleted) before the next seed
            subprocess.run(["rm", "-rf", str(cwd)], check=False)

    rungs = read_rung_choices(ticket_id=seed["id"])
    recs = [r for r in _corpus_records_since(started) if r.get("ticket_id") == seed["id"]]
    wall = "done" if result is not None else ("wall" if rungs else "availability-escape")
    turns = rungs[-1].get("turn_reached") if rungs else None
    log.info("harvest_batch: seed=%s class_intent=%s wall=%s turns=%s ticket_id=%s records=%d",
             seed["id"], seed.get("class_intent"), wall, turns, seed["id"], len(recs))
    return {"id": seed["id"], "class_intent": seed.get("class_intent"), "split": seed.get("split"),
            "wall": wall, "turns": turns, "n_records": len(recs), "started": started}


def run_batch(seeds: list[dict], *, slice_name: str, budget: int, seal_root: Path | None = None,
              checkout: Path | None = None) -> dict:
    """Run every seed, then seal the eval-split transcripts into a held-out slice.

    Returns a report: per-seed summaries, the sealed manifest, and the class-intent distribution.
    Separated from main() so the hermetic plumbing proof can drive it with a stubbed domain.run.
    """
    from unseen_university.devices.inference.eval_slice import EvalSlice

    batch_started = datetime.now(timezone.utc).isoformat()
    summaries = [_run_seed(s, keep=False, checkout=checkout) for s in seeds]

    # Seal ONLY the eval-split seeds' transcripts — the held-out reality-uncoupled slice.
    eval_ids = {s["id"] for s in seeds if s.get("split") == "eval"}
    eval_records = [r for r in _corpus_records_since(batch_started) if r.get("ticket_id") in eval_ids]
    manifest = EvalSlice(slice_name, budget=budget, root=seal_root).seal(eval_records)
    log.info("harvest_batch: sealed slice=%s entries=%d hash=%s (eval seeds=%d)",
             slice_name, manifest["n"], manifest["content_hash"][:12], len(eval_ids))

    dist: dict[str, int] = {}
    for s in summaries:
        dist[s["class_intent"]] = dist.get(s["class_intent"], 0) + 1
    return {"summaries": summaries, "manifest": manifest, "class_distribution": dist,
            "eval_seed_ids": sorted(eval_ids)}


def _preflight_route_ok(endpoint: str) -> bool:
    """Confirm the coding route resolves to the FREE Hex ollama tier before trusting the batch."""
    from unseen_university.devices.inference.device import InferenceDevice
    from unseen_university.devices.inference.domains import resolve_domain

    dev = InferenceDevice()
    dom = resolve_domain("coding")
    if not dom.harvest_mode:
        print("ABORT: UU_HARVEST_MODE did not take — resolved domain is not in harvest mode.")
        return False
    dec = dom.select(dev._rules, task_class="worker", required_difficulty="code")
    route_src = getattr(getattr(dec, "source", None), "name", "?")
    route_cost = getattr(getattr(dec, "model", None), "input_cost_per_1m", None)
    print(f"pre-flight route: source={route_src} cost_per_1m={route_cost} endpoint={endpoint}")
    if route_src != "ollama":
        print(f"ABORT: coding route is '{route_src}', not free Hex ollama — a batch now would be a "
              f"routing artifact (or paid). Bring Hex up / check INFERENCE_ENDPOINT.")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://hex.local:11434", help="ollama/Hex base URL")
    ap.add_argument("--seeds", default=str(_DEFAULT_SEEDS_DIR), help="dir of *.json seed tickets")
    ap.add_argument("--max-turns", type=int, default=12,
                    help="realistic per-loop turn cap (>4 so each transcript carries its distinction)")
    ap.add_argument("--slice-name", default="", help="eval-slice name (default: stuck-corpus-<date>)")
    ap.add_argument("--budget", type=int, default=64, help="eval-slice read budget")
    ap.add_argument("--keep-workdir", action="store_true", help="leave scratch dirs for inspection")
    ap.add_argument("--checkout", nargs="?", const=str(Path.home() / "dicksimnel"),
                    default="", help="run each seed against a throwaway clone at this path (created if "
                    "missing, reset pristine per seed) instead of a tmp scratch — the isolation path. "
                    "Bare flag uses ~/dicksimnel.")
    args = ap.parse_args()

    os.environ["INFERENCE_ENDPOINT"] = args.endpoint
    os.environ["UU_HARVEST_MODE"] = "1"

    if not _preflight_route_ok(args.endpoint):
        return 3

    seeds = _load_seeds(Path(args.seeds))
    if not seeds:
        print(f"ABORT: no seeds in {args.seeds}")
        return 2
    print(f"loaded {len(seeds)} seeds: " +
          ", ".join(f"{s['id']}({s.get('class_intent','?')[:3]}/{s.get('split','?')})" for s in seeds))

    _warm_model(args.endpoint)
    if args.max_turns > 0:
        _pin_low_turn_cap(args.max_turns)

    checkout = Path(args.checkout).expanduser() if args.checkout else None
    if checkout is not None:
        from unseen_university._uu_root import uu_root
        checkout = _ensure_throwaway_clone(checkout, Path(uu_root()))
        print(f"checkout-mode: seeds run against throwaway clone {checkout} (reset pristine per seed)")
    else:
        print("WARNING: no --checkout — using tmp scratch, which devstral IGNORES for the live repo "
              "(contaminated corpus). Only valid for smoke tests, not a real harvest.")

    slice_name = args.slice_name or f"stuck-corpus-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    report = run_batch(seeds, slice_name=slice_name, budget=args.budget, checkout=checkout)

    print("\n=== BATCH REPORT ===")
    for s in report["summaries"]:
        print(f"  {s['id']:<34} class={s['class_intent']:<16} wall={s['wall']:<18} "
              f"turns={s['turns']} records={s['n_records']}")
    m = report["manifest"]
    print(f"\nsealed slice='{slice_name}' entries={m['n']} hash={m['content_hash'][:12]} "
          f"(eval seeds: {', '.join(report['eval_seed_ids'])})")
    print(f"class-intent distribution: {report['class_distribution']}")
    print(f"\nNEXT: Akien human-labels the sealed slice; then T-ds-defeating-question-classifier "
          f"grades against it via budgeted eval_slice reads.")
    return 0 if m["n"] >= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
