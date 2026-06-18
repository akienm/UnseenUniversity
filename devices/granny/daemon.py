"""
GrannyDaemon — rules-engine ticket dispatcher.

Poll the queue for sprint tickets, match each ticket against a YAML rule list
(first-match wins), dispatch to the winning worker.

Rule format (config/granny.yaml):
  rules:
    - when: {tags_any: [Security, ...]}
      route_to: CC.1
    - when: {role_in: [master, guru]}
      route_to: CC.1
    - route_to: CC.1          # no 'when' = default/fallback

  workers:
    CC.1:
      dispatch: bus
      mailbox: cc.1          # worker's IMAP mailbox — replies return to granny.0
      one_at_a_time: true
    DickSimnel.0:
      dispatch: set_worker
      worker_name: dicksimnel

  granny_mailbox: granny.0   # Granny's reply mailbox (default: granny.0)

Bus dispatch (D-cc-shim-assignment-model-2026-06-06):
  1. Granny sends a dispatch envelope to worker mailbox → setstatus dispatched
  2. Worker shim acks → Granny marks acked
  3. Worker shim sends started → Granny marks in_progress
  4. Worker shim sends timeout → Granny marks escalated
  Watchdog: dispatched tickets older than DISPATCH_ACK_TIMEOUT_S → escalated

Granny is transport-agnostic: she sends bus envelopes and sets worker fields.
All tmux/session knowledge lives in the worker's shim (e.g. CCWorkerListener),
not here. See devices/granny/cc_worker_listener.py for CC delivery.

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
_CC_QUEUE = _UU_ROOT / "devlab" / "claudecode" / "cc_queue.py"
_DB_URL = os.environ.get(
    "UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)
_PYTHON = sys.executable
_GRANNY_HOME = Path.home() / ".granny"
_CONFIG_PATH = _UU_ROOT / "config" / "granny.yaml"
_PID_FILE = _GRANNY_HOME / "daemon.pid"

POLL_INTERVAL_S = int(os.environ.get("GRANNY_POLL_INTERVAL", "60"))
_CIRCUIT_STATE_FILE = Path(
    os.environ.get("UU_CIRCUIT_STATE_FILE", str(Path.home() / ".unseen_university" / "circuit_state.json"))
)


# ── Config ────────────────────────────────────────────────────────────────────


def _load_announced_workers() -> dict:
    """Scan ~/.granny/announced/*.json and return a workers dict.

    Reaps stale files from crashed workers via pid liveness check.
    Returns a dict in the same shape as config['workers'] so callers can
    merge: workers_cfg = {**static_cfg, **_load_announced_workers()}.
    Announced entries take precedence over static config — the announce
    file is the canonical source for self-announcing workers.
    """
    from devices.granny.announce_worker import is_alive

    announce_dir = Path.home() / ".granny" / "announced"
    if not announce_dir.exists():
        return {}

    workers: dict = {}
    for path in sorted(announce_dir.glob("*.json")):
        try:
            rec = json.loads(path.read_text())
            pid = rec.get("pid", 0)
            if pid and not is_alive(pid):
                log.info(
                    "Granny: reaping stale announce %s (pid=%d dead)", path.name, pid
                )
                path.unlink(missing_ok=True)
                continue
            worker_id = rec["worker_id"]
            workers[worker_id] = {
                "dispatch": rec.get("dispatch", "bus"),
                "mailbox": rec.get("mailbox", ""),
                "worker_name": rec.get("worker_name", worker_id.lower()),
                "one_at_a_time": rec.get("one_at_a_time", False),
                "cascade_if_idle": rec.get("cascade_if_idle", False),
            }
        except Exception as exc:
            log.warning("Granny: failed to load announcement %s: %s", path.name, exc)

    return workers


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
    # CC workers self-announce; only DickSimnel.0 is static (needs launch_cmd).
    # Routing rules mirror config/granny.yaml — no worker_id hardcoded as fallback.
    return {
        "workers": {
            "DickSimnel.0": {"dispatch": "bus", "mailbox": "dicksimnel.0"},
        },
        "rules": [
            {"when": {"role_in": ["guru"]}, "route_to": "akien"},
            {"when": {"role_in": ["master"]}, "route_to": "CC.1"},
            {"when": {"role_in": ["builder", "creator"]}, "route_to": "DickSimnel.0"},
            {"route_to": "CC.1"},
        ],
    }


# ── Role constants (mirrors cc_queue.py; test_cc_queue_role verifies sync) ────

_VALID_ROLES: frozenset[str] = frozenset(
    {"apprentice", "builder", "creator", "master", "guru"}
)

_WORKER_TO_ROLE: dict[str, str] = {
    "claude": "master",
    "cc": "master",
    "dicksimnel": "builder",
    "igor": "apprentice",
}

# Worker ID → worker name used in ticket metadata (for _cc0_busy() detection)
_WORKER_ID_TO_NAME: dict[str, str] = {
    "CC.0": "claude",
    "DickSimnel.0": "dicksimnel",
}


def _infer_role(t: dict) -> str:
    """Return the role for a ticket, inferring from worker when role is absent.

    Mirrors cc_queue._infer_role — test_cc_queue_role.TestGrannyDeferralWithRole
    verifies these stay in sync with cc_queue constants.
    """
    role = (t.get("role") or "").strip().lower()
    if role in _VALID_ROLES:
        return role
    worker = (t.get("worker") or "").lower()
    return _WORKER_TO_ROLE.get(worker, "apprentice")


# ── Queue ─────────────────────────────────────────────────────────────────────


def _setstatus_direct(tid: str, status: str, worker: str | None = None) -> bool:
    """Set ticket status (and optionally worker) via direct Postgres UPDATE.

    Writes to both clan.memories (legacy) and devlab.tickets (new) so that
    handshake status transitions (dispatched/acked/in_progress/sprint) land
    regardless of which table the ticket lives in.
    Returns True on success, False on error (logs warning, never raises).
    """
    try:
        import psycopg2
        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn:
            with conn.cursor() as cur:
                # clan.memories — metadata JSONB, status inside metadata
                if worker:
                    cur.execute(
                        """UPDATE clan.memories
                           SET metadata = jsonb_set(
                               jsonb_set(metadata, '{status}', %s::jsonb),
                               '{worker}', %s::jsonb
                           )
                           WHERE id = %s""",
                        (f'"{status}"', f'"{worker}"', tid),
                    )
                else:
                    cur.execute(
                        """UPDATE clan.memories
                           SET metadata = jsonb_set(metadata, '{status}', %s::jsonb)
                           WHERE id = %s""",
                        (f'"{status}"', tid),
                    )
                # devlab.tickets — dedicated status column + metadata JSONB for worker
                if worker:
                    cur.execute(
                        """UPDATE devlab.tickets
                           SET status = %s,
                               metadata = jsonb_set(
                                   COALESCE(metadata, '{}'), '{worker}', %s::jsonb
                               )
                           WHERE id = %s""",
                        (status, f'"{worker}"', tid),
                    )
                else:
                    cur.execute(
                        "UPDATE devlab.tickets SET status = %s WHERE id = %s",
                        (status, tid),
                    )
        conn.close()
        log.debug("Granny: _setstatus_direct %s → %s (worker=%s)", tid, status, worker)
        return True
    except Exception as exc:
        log.warning("Granny: _setstatus_direct %s → %s failed: %s", tid, status, exc)
        return False


def _sprint_tickets() -> list[dict]:
    """Load ungated sprint tickets from clan.memories and devlab.tickets. Returns [] on error."""
    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        tickets: dict[str, dict] = {}

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # clan.memories — legacy tickets (metadata JSONB, gate inside metadata)
            cur.execute(
                """SELECT metadata FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' IN ('sprint')
                   AND (metadata->>'gate' IS NULL OR metadata->>'gate' = '')
                   ORDER BY (metadata->>'priority')::float DESC NULLS LAST
                   LIMIT 50"""
            )
            for row in cur.fetchall():
                t = dict(row["metadata"])
                if t.get("id"):
                    tickets[t["id"]] = t

            # devlab.tickets — new tickets (dedicated columns + metadata JSONB for extras)
            cur.execute(
                """SELECT id, title, status, worker, size, tags, description, decision_id, metadata
                   FROM devlab.tickets
                   WHERE status = 'sprint'
                   AND (metadata->>'gate' IS NULL OR metadata->>'gate' = '')
                   ORDER BY (metadata->>'priority')::float DESC NULLS LAST
                   LIMIT 50"""
            )
            for row in cur.fetchall():
                md = row["metadata"] or {}
                t = {
                    "id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "worker": row["worker"] or "claude",
                    "size": row["size"],
                    "tags": row["tags"] or [],
                    "description": row["description"],
                    "decision_id": row["decision_id"],
                    "role": md.get("role", "master"),
                    "priority": md.get("priority", 0.5),
                    "gate": md.get("gate"),
                }
                if row["id"]:
                    tickets[row["id"]] = t  # devlab overrides clan on conflict

        conn.close()
        return sorted(tickets.values(), key=lambda t: -(t.get("priority") or 0.5))
    except Exception as e:
        log.warning("Granny: ticket query failed: %s", e)
        return []


def _cleared_gated_tickets() -> list[dict]:
    """Return gated sprint tickets whose gate has cleared per gate_logic.gate_clear().

    Fetches all gated sprint tickets plus the full ticket status index, evaluates
    each gate, and returns only those whose every predecessor is terminal.
    Logs blocked tickets at DEBUG, cleared ones at INFO.
    Returns [] on any DB error (fail open — never blocks the dispatch cycle).
    """
    try:
        import psycopg2
        import psycopg2.extras
        from unseen_university.gate_logic import gate_clear

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Gated sprint tickets from clan.memories
            cur.execute(
                """SELECT metadata FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' = 'sprint'
                   AND metadata->>'gate' IS NOT NULL
                   AND metadata->>'gate' != ''
                   ORDER BY (metadata->>'priority')::float DESC NULLS LAST
                   LIMIT 50"""
            )
            gated_clan = [dict(r["metadata"]) for r in cur.fetchall()]

            # Gated sprint tickets from devlab.tickets
            cur.execute(
                """SELECT id, title, status, worker, size, tags, description, decision_id, metadata
                   FROM devlab.tickets
                   WHERE status = 'sprint'
                   AND metadata->>'gate' IS NOT NULL
                   AND metadata->>'gate' != ''
                   ORDER BY (metadata->>'priority')::float DESC NULLS LAST
                   LIMIT 50"""
            )
            gated_devlab = []
            for row in cur.fetchall():
                md = row["metadata"] or {}
                gated_devlab.append({
                    "id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "worker": row["worker"] or "claude",
                    "role": md.get("role", "master"),
                    "priority": md.get("priority", 0.5),
                    "gate": md.get("gate"),
                })

            gated = gated_clan + gated_devlab
            if not gated:
                conn.close()
                return []

            # Status index from clan.memories (legacy)
            cur.execute(
                """SELECT metadata->>'id' AS id, metadata->>'status' AS status
                   FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'"""
            )
            all_statuses = [{"id": r["id"], "status": r["status"]} for r in cur.fetchall()]

            # Add devlab.tickets statuses (deduplicated by id, devlab wins)
            cur.execute("SELECT id, status FROM devlab.tickets")
            devlab_statuses = {r["id"]: r["status"] for r in cur.fetchall()}
            all_statuses = [s for s in all_statuses if s["id"] not in devlab_statuses]
            all_statuses += [{"id": k, "status": v} for k, v in devlab_statuses.items()]
        conn.close()
    except Exception as e:
        log.warning("Granny: gated ticket query failed: %s", e)
        return []

    cleared = []
    for t in gated:
        gate_val = t.get("gate", "")
        if gate_clear(gate_val, all_statuses):
            log.info(
                "Granny: gate_cleared|ticket=%s|gate=%s — promoting to dispatch",
                t.get("id", "?"), gate_val,
            )
            cleared.append(t)
        else:
            log.debug(
                "Granny: gate_blocked|ticket=%s|gate=%s",
                t.get("id", "?"), gate_val,
            )
    return cleared


def _worker_busy(worker_names: list[str]) -> bool:
    """True when any named worker has a ticket in dispatched, acked, or in_progress.

    Includes dispatched/acked so that bus-dispatched tickets prevent a second
    dispatch before the handshake completes.
    """
    if not worker_names:
        return False
    try:
        import psycopg2

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(worker_names))
            cur.execute(
                f"""SELECT 1 FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' IN ('dispatched', 'acked', 'in_progress')
                   AND metadata->>'worker' IN ({placeholders})
                   LIMIT 1""",
                worker_names,
            )
            busy = cur.fetchone() is not None
        conn.close()
        return busy
    except Exception:
        return False  # fail open — don't block workers when DB is unreachable


def _cc0_busy() -> bool:
    """True when CC.0 (worker=claude/cc) has a ticket in dispatched/acked/in_progress."""
    return _worker_busy(["claude", "cc"])


# ── Rules engine ──────────────────────────────────────────────────────────────


def match_rule(ticket: dict, rules: list[dict], exact_match: bool = False) -> str | None:
    """First-match rules engine. Returns the target worker_id, or None when exact_match
    is True and no role rule matched.

    Rule shapes:
      {when: {tags_any: [...]}, route_to: X}   — matches if any tag in list
      {when: {role_in: [...]},  route_to: X}   — matches if role in list
      {when: {role: str},       route_to: X}   — exact role match
      {route_to: X}                             — default (no 'when' = always matches)

    exact_match=True: default fallback rules (no 'when') are skipped. Returns None
    when no role rule matched — caller logs a warning and defers the ticket.
    exact_match=False: a catch-all `- route_to: <worker>` rule handles the default;
    returns None only if the rules list has no catch-all (misconfiguration).
    """
    tags = set(ticket.get("tags") or [])
    role = (ticket.get("role") or "").lower()

    for rule in rules:
        when = rule.get("when")
        if when is None:
            if exact_match:
                continue  # skip default catch-all in exact_match mode
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

    if exact_match:
        return None  # no match in exact mode — caller defers the ticket
    # No last-resort hardcode: if rules are configured correctly there is always
    # a catch-all `- route_to: <worker>` entry. Returning None here lets the
    # caller log a warning and defer rather than blindly firing to a hardcoded worker.
    return None


# ── Dispatch ──────────────────────────────────────────────────────────────────


def _dispatch_akien(ticket: dict) -> bool:
    """Hold a guru-role ticket for human attention: set worker=akien, post channel nudge."""
    tid = ticket["id"]
    r = subprocess.run(
        [_PYTHON, str(_CC_QUEUE), "set-worker", "akien", tid],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "UU_HOME_DB_URL": _DB_URL},
    )
    if r.returncode != 0:
        log.warning("Granny: set-worker akien failed for %s: %s", tid, r.stderr[:100])
        return False
    _post_channel(
        f"NEEDS_AKIEN|ticket={tid}|title={ticket.get('title', '?')[:60]}"
    )
    log.info("Granny: %s → needs Akien (guru role — not dispatched to CC or DickSimnel)", tid)
    return True




# ── Bus dispatch + handshake response ────────────────────────────────────────

# Granny resets a dispatched ticket to sprint if no ack arrives within this window.
DISPATCH_ACK_TIMEOUT_S = int(os.environ.get("GRANNY_DISPATCH_ACK_TIMEOUT", "120"))
# Builder cooldown after a dispatch timeout — 10 minutes by default.
GRANNY_BUILDER_COOLDOWN_S = int(os.environ.get("GRANNY_BUILDER_COOLDOWN_S", "600"))
# Minimum gap between consecutive builder launch attempts.
GRANNY_BUILDER_LAUNCH_RETRY_S = int(os.environ.get("GRANNY_BUILDER_LAUNCH_RETRY_S", "60"))

_GRANNY_MAILBOX_DEFAULT = "granny.0"

# Tracks when each worker was last launch-attempted so we don't spam launches.
_last_launch_attempt: dict[str, float] = {}


def _dispatch_bus(
    ticket: dict,
    imap,
    worker_mailbox: str,
    granny_mailbox: str,
    *,
    worker_name: str | None = None,
) -> bool:
    """Send a dispatch envelope to the worker's bus mailbox and mark dispatched.

    worker_name — when provided, also updates the ticket's worker field so that
    per-worker busy checks (_worker_busy) work across poll cycles.

    The handshake is async — Granny does not wait for ack here. Replies arrive
    in granny_mailbox and are processed by _process_handshake_replies on the
    next poll cycle.
    """
    from bus.envelope import Envelope

    tid = ticket["id"]
    env = Envelope.now(
        from_device=granny_mailbox,
        to_device=worker_mailbox,
        payload={
            "kind": "dispatch",
            "ticket_id": tid,
        },
    )
    try:
        imap.append(worker_mailbox, env)
    except Exception as exc:
        log.warning("Granny: bus send failed for %s → %s: %s", tid, worker_mailbox, exc)
        return False

    _setstatus_direct(tid, "dispatched", worker=worker_name)
    log.info(
        "Granny: dispatched %s → %s via bus (granny_mailbox=%s worker=%s)",
        tid, worker_mailbox, granny_mailbox, worker_name,
    )
    return True


def _process_handshake_replies(imap, granny_mailbox: str) -> int:
    """Fetch all pending handshake replies from Granny's mailbox and apply transitions.

    Expected reply kinds (from BaseShim._DispatchHandshake):
      dispatch_ack     → setstatus acked
      dispatch_started → setstatus in_progress
      dispatch_timeout → setstatus escalated

    Returns the number of replies processed.
    """
    try:
        envelopes = imap.fetch_unseen(granny_mailbox)
    except Exception as exc:
        log.warning("Granny: reply fetch failed (mailbox=%s): %s", granny_mailbox, exc)
        return 0

    count = 0
    for env in envelopes:
        payload = getattr(env, "payload", {}) if not isinstance(env, dict) else env.get("payload", {})
        kind = payload.get("kind", "")
        tid = payload.get("ticket_id", "")
        if not tid or kind not in ("dispatch_ack", "dispatch_started", "dispatch_timeout", "dispatch_done"):
            continue

        if kind == "dispatch_done":
            # Builder closed the ticket directly — observability only, no status change needed.
            builder = payload.get("from_device", "?")
            log.info("Granny: builder %s completed %s (dispatch_done)", builder, tid)
            count += 1
            continue

        new_status = {
            "dispatch_ack": "acked",
            "dispatch_started": "in_progress",
            "dispatch_timeout": "escalated",
        }[kind]

        ok = _setstatus_direct(tid, new_status)
        if not ok:
            log.warning(
                "Granny: _setstatus_direct %s failed for %s (see above)", new_status, tid
            )
        else:
            log.info(
                "Granny: handshake reply %s for %s → status=%s",
                kind, tid, new_status,
            )
        count += 1

    return count


def _escalate_stale_dispatched() -> int:
    """Reset tickets stuck in 'dispatched' past DISPATCH_ACK_TIMEOUT_S back to sprint.

    Returns ticket to sprint (not escalated) so any available builder can pick it up.
    Marks the timed-out worker on cooldown to prevent immediate re-dispatch storm.
    Uses updated_at from the DB — no local timestamp file needed.
    Returns the number of tickets reset.
    """
    from devices.granny.availability import mark_unavailable

    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT metadata->>'id' AS tid, metadata->>'worker' AS worker
                   FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' = 'dispatched'
                   AND updated_at::timestamptz < now() - interval '%s seconds'""",
                (DISPATCH_ACK_TIMEOUT_S,),
            )
            stale = [(row["tid"], row.get("worker") or "") for row in cur.fetchall() if row["tid"]]
        conn.close()
    except Exception as exc:
        log.warning("Granny: stale-dispatched query failed: %s", exc)
        return 0

    # Build worker name → worker_id map dynamically from announcements + static defaults.
    _WORKER_NAME_TO_ID: dict[str, str] = {"dicksimnel": "DickSimnel.0", "claude": "CC.0", "cc": "CC.0"}
    announced_dir = Path.home() / ".granny" / "announced"
    if announced_dir.exists():
        for _p in announced_dir.glob("*.json"):
            try:
                _r = json.loads(_p.read_text())
                wname = _r.get("worker_name", "")
                if wname:
                    _WORKER_NAME_TO_ID[wname] = _r["worker_id"]
            except Exception:
                pass

    count = 0
    for tid, worker_name in stale:
        # Reset to sprint + clear worker assignment
        ok_status = _setstatus_direct(tid, "sprint", worker="")
        if not ok_status:
            log.warning("Granny: stale-dispatched reset failed for %s", tid)
            continue

        log.warning(
            "Granny: reset stale-dispatched ticket %s to sprint (no ack within %ds, worker=%s)",
            tid, DISPATCH_ACK_TIMEOUT_S, worker_name,
        )
        count += 1

        # Put the timed-out worker on cooldown so we don't immediately re-dispatch
        worker_id = _WORKER_NAME_TO_ID.get(worker_name.lower())
        if worker_id:
            mark_unavailable(worker_id, cooldown_s=GRANNY_BUILDER_COOLDOWN_S)
            log.info("Granny: %s on cooldown %ds after dispatch timeout", worker_id, GRANNY_BUILDER_COOLDOWN_S)

    return count


_STALE_INPROGRESS_TIMEOUT_S = int(os.environ.get("GRANNY_STALE_INPROGRESS_TIMEOUT", str(2 * 3600)))


def _reset_stale_inprogress() -> int:
    """Reset CC-worker tickets stuck in 'in_progress' past _STALE_INPROGRESS_TIMEOUT_S.

    Covers CC.0 defaults ('claude', 'cc') plus any worker_name from announced workers
    so CC.1 and future CC workers are included automatically.

    Uses cc_queue.py reset --timeout so each reset increments a counter that
    automatically holds the ticket after 3 resets, capping retry-loop token spend.
    Returns the number of tickets reset.
    """
    # Collect CC worker names from static defaults + announcements.
    cc_worker_names = ["claude", "cc"]
    _ann_dir = Path.home() / ".granny" / "announced"
    if _ann_dir.exists():
        for _p in _ann_dir.glob("*.json"):
            try:
                _r = json.loads(_p.read_text())
                wname = _r.get("worker_name", "")
                if wname and wname not in cc_worker_names:
                    cc_worker_names.append(wname)
            except Exception:
                pass

    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            placeholders = ",".join(["%s"] * len(cc_worker_names))
            cur.execute(
                f"""SELECT metadata->>'id' AS tid FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' = 'in_progress'
                   AND metadata->>'worker' IN ({placeholders})
                   AND updated_at::timestamptz < now() - interval '%s seconds'""",
                (*cc_worker_names, _STALE_INPROGRESS_TIMEOUT_S),
            )
            stale = [row["tid"] for row in cur.fetchall() if row["tid"]]
        conn.close()
    except Exception as exc:
        log.warning("Granny: stale-inprogress query failed: %s", exc)
        return 0

    count = 0
    for tid in stale:
        r = subprocess.run(
            [_PYTHON, str(_CC_QUEUE), "reset", "--timeout", tid],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "UU_HOME_DB_URL": _DB_URL},
        )
        if r.returncode != 0:
            log.warning("Granny: stale-inprogress reset failed for %s: %s", tid, r.stderr[:80])
        else:
            log.warning(
                "Granny: reset stale-inprogress ticket %s (stuck >%ds) via --timeout",
                tid, _STALE_INPROGRESS_TIMEOUT_S,
            )
        count += 1

    return count


class _DaemonStatus:
    """Lightweight daemon status object returned by get_daemon()."""

    def is_running(self) -> bool:
        pid_file = Path.home() / ".granny" / "daemon.pid"
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def get_daemon() -> _DaemonStatus:
    """Return a status object for the Granny daemon (checks PID file)."""
    return _DaemonStatus()


def _dicksimnel_available() -> bool:
    """Return True if DickSimnel's .true flag exists and .false flag does not."""
    try:
        flag_dir = Path.home() / ".granny" / "available"
        return (
            (flag_dir / "DickSimnel.0.available.true").exists()
            and not (flag_dir / "DickSimnel.0.available.false").exists()
        )
    except Exception:
        return False


def _post_channel(msg: str) -> None:
    try:
        from unseen_university.channel import post_to_channel
        # push_ws=False: Granny writes directly to Postgres only.
        # The web server's ws_only path previously created a duplicate row
        # when the server was running stale code without ws_only support.
        post_to_channel(msg, author="granny-weatherwax", channel="shared", push_ws=False)
    except Exception as e:
        log.debug("Granny: channel post failed: %s", e)


# ── Cascade tier pickup ────────────────────────────────────────────────────────

# Role tier order from highest to lowest authority.
_TIER_ORDER = ("guru", "master", "creator", "builder", "apprentice")


def _cascade_active_workers(config: dict, tickets: list[dict]) -> dict[str, list[str]]:
    """
    Return {worker_id: [extra_roles]} for workers with cascade_if_idle=true
    whose own native-tier queue is empty.

    Cascade logic: when a worker's primary-role tickets are exhausted, it
    widens its acceptance to roles below it in _TIER_ORDER. The caller uses
    this mapping to override match_rule() when a lower-tier ticket arrives.

    Interface crossing: INFO log per cascade-active worker.
    """
    workers_cfg = config.get("workers", {})
    rules = config.get("rules", [])

    # Build worker → native roles from the rules config
    worker_native: dict[str, set[str]] = {}
    for rule in rules:
        when = rule.get("when", {})
        target = rule.get("route_to", "")
        if "role_in" in when:
            worker_native.setdefault(target, set()).update(
                r.lower() for r in when["role_in"]
            )

    result: dict[str, list[str]] = {}

    for worker_id, wcfg in workers_cfg.items():
        if not isinstance(wcfg, dict) or not wcfg.get("cascade_if_idle"):
            continue

        native = worker_native.get(worker_id, set())
        if not native:
            continue

        # Find the highest tier this worker natively covers
        highest = next((r for r in _TIER_ORDER if r in native), None)
        if not highest:
            continue

        # Skip cascade if own-tier tickets still exist
        own_tier = [t for t in tickets if _infer_role(t) in native]
        if own_tier:
            continue

        # Cascade: absorb roles below own tier
        below = list(_TIER_ORDER[_TIER_ORDER.index(highest) + 1:])
        result[worker_id] = below
        log.info(
            "Granny: cascade_active|worker=%s|native=%s|absorbs=%s",
            worker_id, sorted(native), below,
        )

    return result


# ── Poll cycle ────────────────────────────────────────────────────────────────


def _launch_builder(worker_id: str, worker_cfg: dict) -> None:
    """Launch a builder process when launch_cmd is configured and rate limit allows.

    Detaches the process (start_new_session=True) so it outlives Granny.
    Tracks launch time in _last_launch_attempt to rate-limit retries.
    """
    launch_cmd = worker_cfg.get("launch_cmd")
    if not launch_cmd:
        log.debug("Granny: no launch_cmd for %s — cannot launch", worker_id)
        return
    now = time.time()
    last = _last_launch_attempt.get(worker_id, 0.0)
    if now - last < GRANNY_BUILDER_LAUNCH_RETRY_S:
        log.debug(
            "Granny: %s launch rate-limited (%.0fs since last attempt)",
            worker_id, now - last,
        )
        return
    _last_launch_attempt[worker_id] = now
    try:
        subprocess.Popen(
            launch_cmd,
            shell=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("Granny: launched builder %s: %s", worker_id, launch_cmd)
    except Exception as exc:
        log.warning("Granny: launch_builder %s failed: %s", worker_id, exc)


def run_once(config: dict, *, imap=None) -> None:
    """Single poll cycle. Ticket status is the authoritative state — no side files.

    imap — optional IMAPServer for bus dispatch; when None, bus dispatch is
    skipped and only legacy (tmux_send_keys / set_worker) paths are used.
    """
    from devices.granny.availability import check_and_expire_cooldowns, is_available

    granny_mailbox = config.get("granny_mailbox", _GRANNY_MAILBOX_DEFAULT)
    # Merge static config + self-announced workers; announced workers take precedence.
    # This replaces the static 'workers:' YAML block for self-announcing workers.
    workers_cfg = {**config.get("workers", {}), **_load_announced_workers()}

    # Expire any builder cooldowns before making dispatch decisions.
    check_and_expire_cooldowns(list(workers_cfg.keys()))

    # Process pending handshake replies and run stale-ticket watchdogs
    # before looking for new work, so transitions land before the busy-check.
    if imap is not None:
        replied = _process_handshake_replies(imap, granny_mailbox)
        if replied:
            log.debug("Granny: processed %d handshake reply(s)", replied)
    _escalate_stale_dispatched()
    _reset_stale_inprogress()

    # Compute candidate tickets once: ungated ready tickets + gated tickets whose
    # gate has now cleared. The combined list drives both the launch check and dispatch.
    tickets = _sprint_tickets() + _cleared_gated_tickets()

    # Launch any idle workers that have sprint tickets waiting but aren't running.
    # Only fires when there's actually work to do and the worker isn't on cooldown.
    if tickets:
        from devices.granny.availability import _avail_dir as _get_avail_dir
        avail_dir = _get_avail_dir()
        for wid, wcfg in workers_cfg.items():
            if not isinstance(wcfg, dict):
                continue
            if not is_available(wid) and not (avail_dir / f"{wid}.cooldown_until").exists():
                # Not available and no cooldown file means worker simply isn't running.
                _launch_builder(wid, wcfg)

    rules = config.get("rules", [])
    exact_match = config.get("exact_match", False)

    # Advance all active workflow scripts (external state, persisted per-workflow).
    try:
        from devices.granny.workflow_executor import get_executor
        get_executor().tick(workers_cfg)
    except Exception as exc:
        log.warning("Granny: workflow executor tick failed (non-fatal): %s", exc)

    # Determine which workers have cascade_if_idle active this cycle.
    cascade = _cascade_active_workers(config, tickets)

    # Track workers that already received a ticket this cycle so one_at_a_time is
    # honoured within the cycle — not just via the DB status (which CC hasn't
    # updated yet by the time the second ticket is evaluated).
    dispatched_this_cycle: set[str] = set()

    for ticket in tickets:
        tid = ticket.get("id", "")
        if not tid:
            continue

        status = ticket.get("status", "sprint")
        ticket_role = _infer_role(ticket)

        # Escalated tickets need a master-tier worker — re-run rules as if role=master
        # so the active sprint worker handles them. Never hardcode a specific worker_id.
        if status == "escalated":
            escalated_ticket = {**ticket, "role": "master"}
            target = match_rule(escalated_ticket, rules, exact_match=False)
            if target is None:
                log.warning("Granny: no route for escalated ticket %s — deferring", tid)
                continue
        else:
            target = match_rule(ticket, rules, exact_match=exact_match)
            if target is None:
                # exact_match=True and no role rule fired — defer without dispatching.
                log.warning(
                    "Granny: exact_match_defer|ticket=%s|role=%s|no_route",
                    tid, ticket_role,
                )
                continue

        # Cascade pickup: a higher-tier worker absorbs this ticket when its own
        # tier queue is empty and cascade_if_idle is set for that worker.
        # ticket_role already computed above.
        is_cascade = False
        for cascade_worker, absorb_roles in cascade.items():
            if ticket_role in absorb_roles and cascade_worker != target:
                log.info(
                    "Granny: cascade_pickup|ticket=%s|role=%s|from=%s|to=%s",
                    tid, ticket_role, target, cascade_worker,
                )
                target = cascade_worker
                is_cascade = True
                break

        # guru tickets go to Akien — no availability check, no CC/DickSimnel dispatch
        if target == "akien":
            ok = _dispatch_akien(ticket)
        else:
            wcfg = workers_cfg.get(target, {})

            if not is_available(target):
                log.debug("Granny: %s unavailable — deferring %s", target, tid)
                continue

            # Circuit breaker check — skip and post GRANNY_THROTTLED when open
            try:
                circuit = json.loads(_CIRCUIT_STATE_FILE.read_text())
                if circuit.get(target) == "OPEN":
                    log.info("Granny: %s circuit OPEN — skipping %s", target, tid)
                    _post_channel(f"GRANNY_THROTTLED|reason=circuit_open|worker={target}|ticket={tid}")
                    continue
            except FileNotFoundError:
                pass
            except Exception as exc:
                log.debug("Granny: circuit check failed (non-fatal): %s", exc)

            # Per-worker busy check: route through _cc0_busy for CC.0 worker names
            # so that existing mocks and observability stay consistent. Non-CC
            # workers (e.g. CC.1 with worker_name="cc.1") use the generic path.
            _wname = wcfg.get("worker_name", "")
            _is_busy = (
                _cc0_busy()
                if not _wname or _wname in {"claude", "cc"}
                else _worker_busy([_wname])
            )
            if wcfg.get("one_at_a_time") and (
                target in dispatched_this_cycle or _is_busy
            ):
                log.debug("Granny: %s one-at-a-time — deferring %s", target, tid)
                continue

            dispatch_kind = wcfg.get("dispatch", "set_worker")
            if dispatch_kind == "bus":
                if imap is None:
                    log.warning(
                        "Granny: dispatch=bus configured for %s but no imap — "
                        "skipping %s (start Granny with bus enabled)",
                        target, tid,
                    )
                    continue
                ok = _dispatch_bus(
                    ticket,
                    imap,
                    wcfg.get("mailbox", f"{target.lower().replace('.', '-')}"),
                    granny_mailbox,
                    worker_name=wcfg.get("worker_name"),
                )
            else:
                log.warning(
                    "Granny: dispatch=%s for %s is unsupported — skipping %s",
                    dispatch_kind, target, tid,
                )
                ok = False

        if ok:
            dispatched_this_cycle.add(target)
            # Cascade: update the ticket's worker field — prefer workers_cfg worker_name,
            # fall back to the legacy _WORKER_ID_TO_NAME dict.
            if is_cascade:
                _cascade_wname = (
                    workers_cfg.get(target, {}).get("worker_name")
                    or _WORKER_ID_TO_NAME.get(target)
                )
                if _cascade_wname:
                    _setstatus_direct(tid, "dispatched", worker=_cascade_wname)
            _post_channel(
                f"GRANNY_DISPATCH|ticket={tid}|worker={target}"
                f"|title={ticket.get('title','?')[:60]}"
            )
        else:
            log.warning("Granny: dispatch failed for %s → %s", tid, target)


# ── Main loop ─────────────────────────────────────────────────────────────────


def _make_imap_if_bus_configured(config: dict):
    """Return a connected IMAPServer when any worker uses dispatch=bus; else None."""
    workers_cfg = {**config.get("workers", {}), **_load_announced_workers()}
    needs_bus = any(
        v.get("dispatch") == "bus" for v in workers_cfg.values() if isinstance(v, dict)
    )
    if not needs_bus:
        return None
    try:
        from bus.connection import make_bus_connection
        return make_bus_connection()
    except Exception as exc:
        log.warning("Granny: bus connection failed — CC.0 dispatch unavailable: %s", exc)
        return None


def run_loop() -> None:
    log.info("Granny: rules-engine daemon starting (poll=%ds)", POLL_INTERVAL_S)
    _GRANNY_HOME.mkdir(parents=True, exist_ok=True)
    cycle = 0

    config = _load_config()
    imap = _make_imap_if_bus_configured(config)
    if imap is not None:
        log.info("Granny: bus dispatch enabled (reply mailbox=%s)",
                 config.get("granny_mailbox", _GRANNY_MAILBOX_DEFAULT))

    while True:
        cycle += 1
        config = _load_config()
        log.debug("Granny: poll cycle %d", cycle)
        try:
            run_once(config, imap=imap)
        except Exception as e:
            log.error("Granny: poll cycle %d error: %s", cycle, e)
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
