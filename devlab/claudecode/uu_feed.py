#!/usr/bin/env python3
"""uu_feed — zero-inference reader for a device's feed channels.

Backs `uu device <dev> feed [channel]` (bare CLI) and `/device <dev> feed`
(CC side). It is the read-half of the device two-products split
(D-skills-two-products) and the bare-shell MIRROR of the shim-owned web feed
buttons (T-device-web-feed-channel-buttons). Generalizes and retires the old
`/readigor` skill — any device, any channel, one reader.

Channels (uniform across all rack devices):
  personal  (default) — the device's web-chat feed (the chat window a human has
              with the device); read from the web_server channel store.
  private             — planned, intentionally NOT designed; prints a notice.
  info / warn / debug — the per-device log hierarchy at
              <UU_LOG_ROOT | uu_home()/logs>/<instance>/<stream>/*.json
              (T-per-device-log-hierarchy). WARNING+ collapses to `warn`.

Read-only. No inference. No DB. Runs in a bare shell. Paths resolve at call
time via UU_LOG_ROOT (hermetic-test override) else uu_home(), so the same
reader is testable against a tmp tree and correct in production.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# The three log-level channels are exactly the per-device log streams.
LOG_CHANNELS = ("info", "warn", "debug")
CHANNELS = ("personal", "private", *LOG_CHANNELS)

DEFAULT_LIMIT = 20


def _uu_home() -> Path:
    """Runtime home (~/.unseen_university), derived — never an env var of its own."""
    try:
        from unseen_university._uu_root import uu_home

        return Path(uu_home())
    except Exception:
        return Path(os.path.expanduser("~/.unseen_university"))


def _log_root() -> Path:
    """Per-device log root: UU_LOG_ROOT override (hermetic tests) else uu_home()/logs."""
    env = os.environ.get("UU_LOG_ROOT")
    return Path(env) if env else _uu_home() / "logs"


def resolve_instance(device: str, log_root: Path) -> str:
    """Map a device name to its on-disk log dir.

    The dispatch name is the device dir under devices/ (e.g. `igor`), but the
    log hierarchy is keyed by the runtime instance id (e.g. `Igor-wild-0001`).
    Resolve in order: exact dir, single case-insensitive prefix match,
    IGOR_INSTANCE_ID for igor, else the device name unchanged.
    """
    if (log_root / device).is_dir():
        return device
    if log_root.is_dir():
        matches = [
            p.name
            for p in log_root.iterdir()
            if p.is_dir() and p.name.lower().startswith(device.lower())
        ]
        if len(matches) == 1:
            return matches[0]
        igor_like = sorted(m for m in matches if "igor" in m.lower())
        if device.lower() == "igor" and igor_like:
            return igor_like[-1]
    env = os.environ.get("IGOR_INSTANCE_ID")
    if device.lower() == "igor" and env:
        return env
    return device


def read_log_stream(
    device: str, stream: str, limit: int = DEFAULT_LIMIT, log_root: Path | None = None
) -> list[dict]:
    """Most-recent records from <log_root>/<instance>/<stream>/*.json (oldest→newest)."""
    log_root = log_root or _log_root()
    inst = resolve_instance(device, log_root)
    stream_dir = log_root / inst / stream
    if not stream_dir.is_dir():
        return []
    files = sorted(stream_dir.glob("*.json"), key=lambda p: p.name)[-limit:]
    records: list[dict] = []
    for f in files:
        try:
            records.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue  # a single corrupt record never breaks the feed (fail-soft)
    return records


def read_personal(
    device: str, limit: int = DEFAULT_LIMIT, home: Path | None = None
) -> list[dict]:
    """Most-recent web-chat messages for the device from the channel store."""
    home = home or _uu_home()
    chan = home / "local" / "cc_channel" / "messages.jsonl"
    if not chan.is_file():
        return []
    records: list[dict] = []
    for line in chan.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    dev = device.lower()
    scoped = [r for r in records if dev in str(r.get("author", "")).lower()]
    # Fall back to the whole channel when nothing is author-scoped to the device,
    # so a device with traffic but no self-authored lines still shows its window.
    return (scoped or records)[-limit:]


def _fmt_log(r: dict) -> str:
    return f"  {r.get('ts', '?')}  {str(r.get('level', '?')):7}  {r.get('message', '')}"


def _fmt_chat(r: dict) -> str:
    return f"  {r.get('ts', '?')}  {r.get('author', '?')}: {r.get('content', '')}"


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print(
            "usage: feed <device> [channel]  (channels: " + ", ".join(CHANNELS) + ")",
            file=sys.stderr,
        )
        return 2

    device = args[0]
    channel = (args[1] if len(args) > 1 else "personal").lower()

    if channel not in CHANNELS:
        print(
            f"uu feed: unknown channel '{channel}' for '{device}' — "
            f"channels: {', '.join(CHANNELS)}",
            file=sys.stderr,
        )
        return 1

    if channel == "private":
        print(
            f"{device} feed — private: not yet designed (intentionally). "
            "No private feed to show."
        )
        return 0

    print(f"{device} feed — {channel}")

    if channel in LOG_CHANNELS:
        records = read_log_stream(device, channel)
        if not records:
            print(f"  (no {channel} records)")
        for r in records:
            print(_fmt_log(r))
        return 0

    records = read_personal(device)
    if not records:
        print("  (no personal feed messages)")
    for r in records:
        print(_fmt_chat(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
