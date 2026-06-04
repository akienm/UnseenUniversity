"""
GrannyDaemon — rules-engine ticket dispatcher.

Poll the queue for sprint tickets, match each ticket against a YAML rule list
(first-match wins), dispatch to the winning worker.

Rule format (config/granny.yaml):
  rules:
    - when: {tags_any: [Security, ...]}
      route_to: CC.0
    - when: {role_in: [master, guru]}
      route_to: CC.0
    - route_to: CC.0          # no 'when' = default/fallback

  workers:
    CC.0:
      dispatch: tmux_send_keys
      session: claude-main
      one_at_a_time: true
    DickSimnel.0:
      dispatch: set_worker
      worker_name: dicksimnel

Run as: python -m devices.granny.daemon
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

_UU_ROOT = Path(__file__).resolve().parents[2]
_CC_QUEUE = _UU_ROOT / "lab" / "claudecode" / "cc_queue.py"
_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)
_PYTHON = sys.executable
_GRANNY_HOME = Path.home() / ".granny"
_DISPATCHED_FILE = _GRANNY_HOME / "dispatched_cycle.json"
_CONFIG_PATH = _UU_ROOT / "config" / "granny.yaml"
_PID_FILE = _GRANNY_HOME / "daemon.pid"

POLL_INTERVAL_S = int(os.environ.get("GRANNY_POLL_INTERVAL", "60"))


# ── Config ────────────────────────────────────────────────────────────────────


def _load_config() -> dict:
    for path in (_CONFIG_PATH, Path.home() / ".granny" / "granny.yaml"):
        if path.exists():
            try:
                import yaml
                return yaml.safe_load(path.read_text()) or {}
            except Exception as e:
                log.warning("Granny: config load failed (%s): %s", path, e)
    log.warning("Granny: no granny.yaml found — using built-in defaults")
    return _default_config()


def _default_config() -> dict:
    return {
        "workers": {
            "CC.0": {"dispatch": "tmux_send_keys", "session": "claude-main", "one_at_a_time": True},
            "DickSimnel.0": {"dispatch": "set_worker", "worker_name": "dicksimnel"},
        },
        "rules": [
            {"when": {"tags_any": ["Security", "Provenance", "Auth", "Brainstem", "Database"]}, "route_to": "CC.0"},
            {"when": {"role_in": ["guru"]}, "route_to": "akien"},
            {"when": {"role_in": ["master"]}, "route_to": "CC.0"},
            {"when": {"role_in": ["builder", "creator"]}, "route_to": "DickSimnel.0"},
            {"route_to": "CC.0"},
        ],
    }


# ── Queue ─────────────────────────────────────────────────────────────────────


def _sprint_tickets() -> list[dict]:
    """Load sprint tickets directly from Postgres. Returns [] on error."""
    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT metadata FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' = 'sprint'
                   AND (metadata->>'gate' IS NULL OR metadata->>'gate' = '')
                   ORDER BY (metadata->>'priority')::float DESC NULLS LAST
                   LIMIT 50"""
            )
            rows = cur.fetchall()
        conn.close()
        return [dict(r["metadata"]) for r in rows]
    except Exception as e:
        log.warning("Granny: ticket query failed: %s", e)
        return []


def _cc0_busy() -> bool:
    """True when a worker=claude ticket is already in_progress."""
    try:
        import psycopg2

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' = 'in_progress'
                   AND metadata->>'worker' IN ('claude', 'cc')
                   LIMIT 1"""
            )
            busy = cur.fetchone() is not None
        conn.close()
        return busy
    except Exception:
        return False  # fail open — don't block CC when DB is unreachable


# ── Rules engine ──────────────────────────────────────────────────────────────


def match_rule(ticket: dict, rules: list[dict]) -> str:
    """First-match rules engine. Returns the target worker_id.

    Rule shapes:
      {when: {tags_any: [...]}, route_to: X}   — matches if any tag in list
      {when: {role_in: [...]},  route_to: X}   — matches if role in list
      {when: {role: str},       route_to: X}   — exact role match
      {route_to: X}                             — default (no 'when' = always matches)
    """
    tags = set(ticket.get("tags") or [])
    role = (ticket.get("role") or "").lower()

    for rule in rules:
        when = rule.get("when")
        if when is None:
            return rule["route_to"]  # default/fallback
        if "tags_any" in when and tags & set(when["tags_any"]):
            log.info(
                "Granny: %s → %s (HIGH-inertia tags: %s)",
                ticket.get("id"),
                rule["route_to"],
                sorted(tags & set(when["tags_any"])),
            )
            return rule["route_to"]
        if "role_in" in when and role in {r.lower() for r in when["role_in"]}:
            return rule["route_to"]
        if "role" in when and role == when["role"].lower():
            return rule["route_to"]

    return "CC.0"  # last-resort default


# ── Dispatch ──────────────────────────────────────────────────────────────────


def _dispatch_cc0(ticket: dict, session: str = "claude-main") -> bool:
    tid = ticket["id"]
    check = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True)
    if check.returncode != 0:
        log.warning("Granny: tmux session %r not found — cannot dispatch %s", session, tid)
        return False
    subprocess.run(
        ["tmux", "send-keys", "-t", session, f"\r\r\r/sprint-ticket {tid}\r"],
        check=False,
    )
    # Mark in_progress immediately — dispatch IS assignment (CLAUDE.md).
    # Without this, _cc0_busy() returns False on the next poll cycle because
    # CC hasn't had time to run setstatus yet, causing a second ticket to fire.
    r = subprocess.run(
        [_PYTHON, str(_CC_QUEUE), "setstatus", tid, "in_progress"],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "IGOR_HOME_DB_URL": _DB_URL},
    )
    if r.returncode != 0:
        log.warning("Granny: setstatus in_progress failed for %s: %s", tid, r.stderr[:80])
    log.info("Granny: dispatched %s → CC.0 (send-keys → %s)", tid, session)
    return True


def _dispatch_akien(ticket: dict) -> bool:
    """Hold a guru-role ticket for human attention: set worker=akien, post channel nudge."""
    tid = ticket["id"]
    r = subprocess.run(
        [_PYTHON, str(_CC_QUEUE), "set-worker", "akien", tid],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "IGOR_HOME_DB_URL": _DB_URL},
    )
    if r.returncode != 0:
        log.warning("Granny: set-worker akien failed for %s: %s", tid, r.stderr[:100])
        return False
    _post_channel(
        f"NEEDS_AKIEN|ticket={tid}|title={ticket.get('title', '?')[:60]}"
    )
    log.info("Granny: %s → needs Akien (guru role — not dispatched to CC or DickSimnel)", tid)
    return True


def _dispatch_dicksimnel(ticket: dict, worker_name: str = "dicksimnel") -> bool:
    tid = ticket["id"]
    r = subprocess.run(
        [_PYTHON, str(_CC_QUEUE), "set-worker", worker_name, tid],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "IGOR_HOME_DB_URL": _DB_URL},
    )
    if r.returncode != 0:
        log.warning("Granny: set-worker failed for %s: %s", tid, r.stderr[:100])
        return False
    log.info("Granny: dispatched %s → DickSimnel.0 (set-worker %s)", tid, worker_name)
    return True


def _post_channel(msg: str) -> None:
    try:
        from unseen_university.channel import post_to_channel
        post_to_channel(msg, author="granny-weatherwax", channel="shared")
    except Exception as e:
        log.debug("Granny: channel post failed: %s", e)


# ── Dispatched-set persistence ────────────────────────────────────────────────


def _load_dispatched() -> set[str]:
    try:
        data = json.loads(_DISPATCHED_FILE.read_text())
        return set(data.get("ids", []))
    except Exception:
        return set()


def _save_dispatched(ids: set[str]) -> None:
    try:
        _DISPATCHED_FILE.write_text(json.dumps({"ids": sorted(ids)}))
    except Exception as e:
        log.warning("Granny: save dispatched failed: %s", e)


# ── Poll cycle ────────────────────────────────────────────────────────────────


def run_once(config: dict, dispatched: set[str]) -> set[str]:
    """Single poll cycle. Returns updated dispatched set."""
    from devices.granny.availability import is_available

    rules = config.get("rules", [])
    workers_cfg = config.get("workers", {})
    tickets = _sprint_tickets()
    new_dispatched = set(dispatched)

    # Track workers that already received a ticket this cycle so one_at_a_time is
    # honoured within the cycle — not just via the DB status (which CC hasn't
    # updated yet by the time the second ticket is evaluated).
    dispatched_this_cycle: set[str] = set()

    for ticket in tickets:
        tid = ticket.get("id", "")
        if not tid or tid in dispatched:
            continue

        target = match_rule(ticket, rules)

        # guru tickets go to Akien — no availability check, no CC/DickSimnel dispatch
        if target == "akien":
            ok = _dispatch_akien(ticket)
        else:
            wcfg = workers_cfg.get(target, {})

            if not is_available(target):
                log.debug("Granny: %s unavailable — deferring %s", target, tid)
                continue

            if wcfg.get("one_at_a_time") and (
                target in dispatched_this_cycle or _cc0_busy()
            ):
                log.debug("Granny: %s one-at-a-time — deferring %s", target, tid)
                continue

            dispatch_kind = wcfg.get("dispatch", "set_worker")
            if dispatch_kind == "tmux_send_keys":
                ok = _dispatch_cc0(ticket, wcfg.get("session", "claude-main"))
            else:
                ok = _dispatch_dicksimnel(ticket, wcfg.get("worker_name", "dicksimnel"))

        if ok:
            new_dispatched.add(tid)
            dispatched_this_cycle.add(target)
            _post_channel(
                f"GRANNY_DISPATCH|ticket={tid}|worker={target}"
                f"|title={ticket.get('title','?')[:60]}"
            )
        else:
            log.warning("Granny: dispatch failed for %s → %s", tid, target)

    return new_dispatched


# ── Main loop ─────────────────────────────────────────────────────────────────


def run_loop() -> None:
    log.info("Granny: rules-engine daemon starting (poll=%ds)", POLL_INTERVAL_S)
    _GRANNY_HOME.mkdir(parents=True, exist_ok=True)
    dispatched = _load_dispatched()
    cycle = 0

    while True:
        cycle += 1
        config = _load_config()
        log.debug("Granny: poll cycle %d (%d dispatched so far)", cycle, len(dispatched))
        try:
            dispatched = run_once(config, dispatched)
        except Exception as e:
            log.error("Granny: poll cycle %d error: %s", cycle, e)
        _save_dispatched(dispatched)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _GRANNY_HOME.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))

    def _handle_sig(sig, _frame):
        log.info("Granny: signal %s — exiting", sig)
        _PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    try:
        run_loop()
    finally:
        _PID_FILE.unlink(missing_ok=True)
