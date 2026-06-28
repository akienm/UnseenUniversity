"""
tmux_face.py — tmux transport face for the Claude device (T-swarm-tmux-face).

Outbound: reads CC's JSONL conversation transcripts (preferred, no escape codes)
and produces bus Envelopes for each turn. Falls back to `tmux capture-pane`
when no transcript path is available.

Inbound: takes a bus Envelope and injects its message into the target tmux
session via `tmux send-keys` with an attribution prefix so the receiving
agent can distinguish injected messages from primary user input.

Attribution format: "<sender>: <message>"
Example: "igor: What's the status on T-swarm-channel-mechanism?"

Transcript format expected (one JSON object per line):
  {"role": "assistant"|"user", "content": "<text>", ...}
Lines missing "role" or "content" are silently skipped.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from unseen_university.devices.bus.envelope import Envelope

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# ── Outbound: JSONL transcript → Envelopes ────────────────────────────────────


def read_jsonl_transcript(
    path: str | Path,
    from_device: str = "CC.0",
    to_device: str = "shared",
) -> list[Envelope]:
    """
    Parse a CC JSONL transcript file and emit one Envelope per conversation turn.

    Each line that has both "role" and "content" fields produces an Envelope.
    Lines missing either field, or that cannot be parsed, are silently skipped.

    Args:
        path:        Path to the JSONL transcript file.
        from_device: Bus address of the emitting agent (default: "CC.0").
        to_device:   Destination bus address (default: "shared").
    """
    envelopes: list[Envelope] = []
    p = Path(path)
    if not p.exists():
        log.debug("transcript not found: %s", path)
        return envelopes

    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = obj.get("role") or obj.get("type")
        content = obj.get("content")
        if not role or not content:
            continue
        payload: dict = {"kind": "transcript_turn", "role": role, "content": content}
        for key in ("uuid", "ts", "timestamp"):
            if key in obj:
                payload[key] = obj[key]
        envelopes.append(
            Envelope.now(from_device=from_device, to_device=to_device, payload=payload)
        )

    return envelopes


def capture_pane(target: str) -> str:
    """
    Run `tmux capture-pane -pt <target>` and return the raw output.

    Returns an empty string on any subprocess failure. Callers should
    prefer JSONL transcripts over this — capture-pane output contains
    terminal escape codes.
    """
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", target],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
        log.warning(
            "capture-pane failed for %r (rc=%d): %s",
            target,
            result.returncode,
            result.stderr.strip(),
        )
        return ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.warning("capture-pane error for %r: %s", target, exc)
        return ""


def capture_pane_as_envelope(
    target: str,
    from_device: str = "CC.0",
    to_device: str = "shared",
) -> Envelope | None:
    """
    Capture current tmux pane content and wrap it as a single Envelope.

    Returns None when capture-pane produces no output.
    """
    text = capture_pane(target)
    if not text.strip():
        return None
    return Envelope.now(
        from_device=from_device,
        to_device=to_device,
        payload={"kind": "pane_capture", "target": target, "content": text},
    )


# ── Inbound: Envelope → tmux send-keys ────────────────────────────────────────


def send_to_session(
    target: str,
    sender: str,
    message: str,
    enter: bool = True,
) -> bool:
    """
    Inject a message into a tmux session via send-keys with attribution.

    The injected text is prefixed as "<sender>: <message>" so the receiving
    agent can distinguish injected messages from primary user input.

    Args:
        target:  tmux target (e.g. "claude-main" or "session:window.pane").
        sender:  Logical name of the sending agent (e.g. "igor").
        message: The message text to inject.
        enter:   When True (default), send ENTER after the text.

    Returns True on success, False on subprocess failure.
    """
    attributed = f"{sender}: {message}"
    cmd = ["tmux", "send-keys", "-t", target, attributed]
    if enter:
        cmd.append("Enter")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            log.debug("send-keys to %r: %r", target, attributed)
            return True
        log.warning(
            "send-keys failed for %r (rc=%d): %s",
            target,
            result.returncode,
            result.stderr.strip(),
        )
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.warning("send-keys error for %r: %s", target, exc)
        return False


def deliver_envelope(envelope: Envelope, target: str) -> bool:
    """
    Deliver an inbound bus Envelope to a tmux session via send-keys.

    Extracts the message from envelope.payload["content"] (or "body" as
    fallback), attributes it as "<from_device>: <content>", and injects.

    Returns True on successful delivery, False otherwise.
    """
    sender = envelope.from_device or "unknown"
    payload = envelope.payload or {}
    message = (
        payload.get("content") or payload.get("body") or payload.get("message") or ""
    )
    if not message:
        log.debug(
            "deliver_envelope: empty message in payload from %r — skipping", sender
        )
        return False
    return send_to_session(target=target, sender=sender, message=str(message))
