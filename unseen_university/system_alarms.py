"""system_alarms — the proxy (and every device) gets a mouth.

A *system alarm* is the Number 5 Crossbar trouble-ticket model: a should-not-
happen event drops an **unignorable, deduped, flat-file artifact** instead of a
log line that drowns in the noise. (Name: ``system_alarm`` — not "trouble
ticket"/"ttix"; that was the metaphor.)

Design (D-system-alarms-and-tier-requests-2026-06-23):
- **Flat-file, NOT Postgres.** A DB-down event must itself be alarmable, so the
  alarm can never depend on the DB. Files live under
  ``$IGOR_HOME/operations/system_alarms/`` (open) and ``.../archive/`` (closed /
  aged-out — the "is this chronic?" history). Paths PRE-APPROVED by Akien.
- **Deduped by signature.** The signature IS the subject
  (``no-provider:<tier>``, ``specific-model:<model>``, ``canary-failed:<provider>``):
  the 2nd/Nth occurrence increments a count, never a new file.
- **Caller breakdown == the punch-list.** Each alarm carries ``{caller: count}``
  plus aggregate count and first/last seen — the authoritative list of call
  sites to fix.
- **Self-clearing.** A caller quiet past a window drops off the breakdown
  (``prune_stale``); when the breakdown empties, the alarm ages out to archive.
  The alarm disappears when the problem is actually fixed — which is how you
  know it is.

``raise_alarm()`` is the real, framework-agnostic primitive: it drops/dedups the
artifact AND emits a normal log line (intercept-and-complete, collapsed into one
explicit call — you don't get an alarm by accident, and an alarm is always also
a greppable log). It is **fail-soft**: a broken alarm drop never raises into the
caller, and the log line is emitted regardless.

Notification on new/reopened alarms (T-system-alarms-notify) and the ``uu
alarms`` view (T-uu-alarms-cli) are separate consumers built on this primitive.
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger("unseen_university.system_alarms")

# A caller silent for longer than this is pruned from the breakdown; when the
# breakdown empties, the alarm ages out. Default chosen so a genuinely-fixed
# call site clears within a day, while a still-firing one stays visible.
DEFAULT_CALLER_QUIET = timedelta(hours=24)

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


# ── Paths ───────────────────────────────────────────────────────────────────


def _igor_home() -> Path:
    """Runtime data dir (``~/.unseen_university``) via :func:`uu_home` — derived,
    NOT an env var; tests redirect by monkeypatching ``uu_home`` (T-uu-eliminate-igor-home-env)."""
    return Path(uu_home())


def alarms_dir() -> Path:
    """Open-alarm directory (created on demand by writers)."""
    return _igor_home() / "operations" / "system_alarms"


def archive_dir() -> Path:
    """Closed / aged-out alarm directory (the chronic-history ledger)."""
    return alarms_dir() / "archive"


def _filename(signature: str) -> str:
    """Deterministic, filesystem-safe filename for a signature.

    Signatures are controlled (``specific-model:<model>`` etc.), so a simple
    sanitize is collision-safe in practice; the original signature is preserved
    inside the JSON either way.
    """
    safe = _SAFE_RE.sub("_", signature).strip("_") or "alarm"
    return f"{safe[:120]}.json"


def _open_path(signature: str) -> Path:
    return alarms_dir() / _filename(signature)


def _archive_path(signature: str) -> Path:
    return archive_dir() / _filename(signature)


# ── Atomic + locked file I/O ──────────────────────────────────────────────────


def _atomic_write(path: Path, payload: dict) -> None:
    """Write JSON to path via tmp + rename — never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".alarm_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _signature_lock(signature: str) -> Iterator[None]:
    """Serialize read-modify-write on one signature across processes.

    A lock file + ``flock`` (Linux) makes the dedup increment lost-update-safe
    when several device processes alarm the same signature at once. Degrades to
    a no-op lock if ``fcntl`` is unavailable.
    """
    alarms_dir().mkdir(parents=True, exist_ok=True)
    lock_path = alarms_dir() / (_filename(signature) + ".lock")
    fh = open(lock_path, "w")
    try:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        fh.close()


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── Result ────────────────────────────────────────────────────────────────────


class SystemAlarmFatal(Exception):
    """Raised by ``raise_alarm(fatal=True)`` AFTER the alarm is reported, to halt
    a caller that must not continue. Never raised when ``fatal=False``."""


@dataclass
class AlarmResult:
    """Outcome of a ``raise_alarm`` call.

    ``status`` is ``new`` (first time), ``incremented`` (already open),
    ``reopened`` (a previously-archived signature recurred), or ``error`` (the
    drop failed — the log line still went out). The notify consumer fires the
    loud channel on ``new`` / ``reopened`` only.
    """

    signature: str
    status: str
    count: int


# ── The primitive ──────────────────────────────────────────────────────────────


def raise_alarm(
    signature: str,
    caller: str,
    message: str,
    *,
    level: str = "ERROR",
    emit_log: bool = True,
    fatal: bool = False,
    now: Optional[datetime] = None,
) -> AlarmResult:
    """Drop/dedup a system alarm AND emit a normal log line.

    Fail-soft by default (``fatal=False``): never raises. When ``fatal=True``,
    the caller must not continue — the alarm is dropped/reported and logged
    first, THEN ``SystemAlarmFatal`` is raised (report-then-halt).

    Args:
        signature: the dedup subject, e.g. ``"no-provider:worker"`` or
            ``"specific-model:anthropic/claude-haiku-4-5"``.
        caller: who fired it (module/dotted name) — goes in the punch-list.
        message: human-readable one-liner for the log + ``last_message``.
        level: log severity name (default ``"ERROR"``).
        emit_log: also emit the normal log line (default True).
        fatal: if True, raise ``SystemAlarmFatal`` after reporting (default False).
        now: injectable clock for tests.
    """
    now = now or datetime.now(timezone.utc)
    status = "error"
    count = 0
    try:
        with _signature_lock(signature):
            path = _open_path(signature)
            existing = _read(path)
            if existing is not None:
                status = "incremented"
                rec = existing
                rec["count"] = int(rec.get("count", 0)) + 1
                callers = rec.setdefault("callers", {})
                callers[caller] = int(callers.get(caller, 0)) + 1
                rec.setdefault("caller_last_seen", {})[caller] = now.isoformat()
                rec["last_seen"] = now.isoformat()
                rec["last_message"] = message
                rec["level"] = level
            else:
                status = "reopened" if _archive_path(signature).exists() else "new"
                rec = {
                    "signature": signature,
                    "count": 1,
                    "callers": {caller: 1},
                    "caller_last_seen": {caller: now.isoformat()},
                    "first_seen": now.isoformat(),
                    "last_seen": now.isoformat(),
                    "level": level,
                    "last_message": message,
                }
                if status == "reopened":
                    rec["reopened_at"] = now.isoformat()
            count = int(rec["count"])
            _atomic_write(path, rec)
    except Exception as exc:  # fail-soft: a broken alarm must never break the caller
        # Surface the drop failure itself honestly (CP1 — not "new" when nothing
        # was written), but never raise into the caller.
        status = "error"
        log.error("system_alarm: drop failed for signature=%s: %s", signature, exc)

    if emit_log:
        levelno = logging.getLevelName(level.upper())
        if not isinstance(levelno, int):
            levelno = logging.ERROR
        log.log(
            levelno,
            "SYSTEM_ALARM|signature=%s|caller=%s|count=%s|fatal=%s|%s",
            signature,
            caller,
            count,
            fatal,
            message,
        )
    if fatal:
        # Report-then-halt: the drop + log above already ran, so the alarm is
        # durable before we stop the caller. Raised only when fatal=True.
        raise SystemAlarmFatal(f"{signature}: {message}")
    return AlarmResult(signature=signature, status=status, count=count)


# ── Views + lifecycle ──────────────────────────────────────────────────────────


def get_alarm(signature: str) -> Optional[dict]:
    """Return the open alarm record for a signature, or None."""
    return _read(_open_path(signature))


def mark_notified(signature: str, *, now: Optional[datetime] = None) -> bool:
    """Stamp ``notified_at`` on an open alarm (the out-of-band notifier did its nag).

    A reopened alarm is recreated without ``notified_at``, so it re-nags; an
    incremented one keeps its stamp, so it does not. Fail-soft: returns False on
    any I/O error rather than raising.
    """
    now = now or datetime.now(timezone.utc)
    try:
        with _signature_lock(signature):
            rec = _read(_open_path(signature))
            if rec is None:
                return False
            rec["notified_at"] = now.isoformat()
            _atomic_write(_open_path(signature), rec)
            return True
    except Exception as exc:
        log.error("system_alarm: mark_notified failed for signature=%s: %s", signature, exc)
        return False


def list_alarms() -> list[dict]:
    """All open alarms, most-recently-seen first."""
    d = alarms_dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for f in d.glob("*.json"):
        rec = _read(f)
        if rec is not None:
            out.append(rec)
    out.sort(key=lambda r: r.get("last_seen", ""), reverse=True)
    return out


def list_archived() -> list[dict]:
    """All archived (closed / aged-out) alarms, most-recently-seen first."""
    d = archive_dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for f in d.glob("*.json"):
        rec = _read(f)
        if rec is not None:
            out.append(rec)
    out.sort(key=lambda r: r.get("closed_at", r.get("last_seen", "")), reverse=True)
    return out


def close_alarm(signature: str, *, now: Optional[datetime] = None) -> bool:
    """Move an open alarm to the archive (the chronic-history ledger).

    Returns True if an open alarm was archived, False if none existed.
    Fail-soft: returns False on any I/O error rather than raising.
    """
    now = now or datetime.now(timezone.utc)
    try:
        with _signature_lock(signature):
            src = _open_path(signature)
            rec = _read(src)
            if rec is None:
                return False
            rec["closed_at"] = now.isoformat()
            _atomic_write(_archive_path(signature), rec)
            src.unlink(missing_ok=True)
            return True
    except Exception as exc:
        log.error("system_alarm: close failed for signature=%s: %s", signature, exc)
        return False


def prune_stale(
    *,
    now: Optional[datetime] = None,
    caller_quiet: timedelta = DEFAULT_CALLER_QUIET,
) -> dict:
    """Self-clear: drop callers quiet past ``caller_quiet``; age out emptied alarms.

    A caller whose ``caller_last_seen`` is older than ``now - caller_quiet`` is
    removed from the breakdown (it's presumed fixed). When the breakdown empties,
    the alarm is archived (it disappears, which is how you know it's resolved).
    Returns ``{"callers_pruned": int, "alarms_aged_out": int}``.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - caller_quiet
    callers_pruned = 0
    aged_out = 0
    for rec in list_alarms():
        signature = rec.get("signature")
        if not signature:
            continue
        try:
            with _signature_lock(signature):
                cur = _read(_open_path(signature))
                if cur is None:
                    continue
                seen = cur.get("caller_last_seen", {})
                callers = cur.get("callers", {})
                stale = [
                    c
                    for c, ts in seen.items()
                    if _parse(ts) is not None and _parse(ts) < cutoff
                ]
                for c in stale:
                    seen.pop(c, None)
                    callers.pop(c, None)
                    callers_pruned += 1
                if not callers:
                    cur["aged_out_at"] = now.isoformat()
                    _atomic_write(_archive_path(signature), cur)
                    _open_path(signature).unlink(missing_ok=True)
                    aged_out += 1
                elif stale:
                    cur["count"] = sum(int(v) for v in callers.values())
                    _atomic_write(_open_path(signature), cur)
        except Exception as exc:
            log.error("system_alarm: prune failed for signature=%s: %s", signature, exc)
    return {"callers_pruned": callers_pruned, "alarms_aged_out": aged_out}


def _parse(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
