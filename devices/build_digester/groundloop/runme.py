"""
Build-log digester daemon — Ground Loop plugin (daemon mode).

Tails cc_channel/log.jsonl and datacenter_logs/queue/trace/*.jsonl,
extracts ticket-keyed events, and upserts them into devlab.build_digest.

Cursor positions (byte offsets) are persisted to
~/.unseen_university/build_digester/cursors.json so restarts resume cleanly.

AR-009: logs every state change (start, stop, each poll cycle result) at INFO.
"""

import glob
from unseen_university._uu_root import uu_home
import json
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = int(os.environ.get("BUILD_DIGESTER_POLL_INTERVAL", "30"))
_RETRY_DELAY_S = int(os.environ.get("BUILD_DIGESTER_RETRY_DELAY", "30"))

_IGOR_HOME = uu_home()
_CURSOR_DIR = os.path.join(_IGOR_HOME, "build_digester")
_CURSOR_FILE = os.path.join(_CURSOR_DIR, "cursors.json")

_CC_LOG = os.path.expanduser("~/.unseen_university/cc_channel/log.jsonl")
_UU_ROOT = os.environ.get(
    "UU_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)
_QUEUE_TRACE_GLOB = os.path.join(_UU_ROOT, "datacenter_logs", "queue", "trace", "*.jsonl")

_stop_evt = threading.Event()


def _load_cursors() -> dict:
    try:
        with open(_CURSOR_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cursors(cursors: dict) -> None:
    os.makedirs(_CURSOR_DIR, exist_ok=True)
    tmp = _CURSOR_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cursors, f)
    os.replace(tmp, _CURSOR_FILE)


def _poll_once(store, cursors: dict) -> dict:
    """Read all log sources, upsert new events, return updated cursors."""
    from devices.build_digester.log_parser import parse_log_file

    total = 0
    sources = [_CC_LOG] + sorted(glob.glob(_QUEUE_TRACE_GLOB))

    for src in sources:
        if not os.path.exists(src):
            continue
        offset = cursors.get(src, 0)
        events, new_offset = parse_log_file(src, start_offset=offset)
        if events:
            for evt in events:
                try:
                    store.upsert_event(evt)
                    total += 1
                except Exception as exc:
                    log.error("build_digester: upsert failed for %s: %s", evt.get("ticket_id"), exc)
            cursors[src] = new_offset
        elif new_offset != offset:
            cursors[src] = new_offset

    if total:
        log.info("build_digester: poll upserted %d events", total)
    return cursors


def start() -> None:
    from devices.build_digester.digest_store import DigestStore

    _stop_evt.clear()
    log.info("build_digester: starting")

    store = DigestStore()
    try:
        store.ensure_tables()
    except Exception as exc:
        log.error("build_digester: ensure_tables failed: %s — will retry on first poll", exc)

    cursors = _load_cursors()

    while not _stop_evt.is_set():
        try:
            cursors = _poll_once(store, cursors)
            _save_cursors(cursors)
        except Exception as exc:
            log.error("build_digester: poll error: %s — retry in %ds", exc, _RETRY_DELAY_S)
        _stop_evt.wait(_POLL_INTERVAL_S)

    log.info("build_digester: stopped")


def stop() -> None:
    log.info("build_digester: stop called")
    _stop_evt.set()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start()
