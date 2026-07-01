"""
Live keystone smoke for DickSimnel (T-ds-smoke-pytest / D-ds-first-live-build-on-hex).

Drives the smallest real build path — DickSimnelDevice._run_inference — against the
REAL devstral-small-2:24b on Hex (10.0.0.100), and asserts the model emitted a native
tool_call, executed a Write, and returned a DONE envelope. This is the first proven
exists→builds→closes cycle on free local inference.

Skip-soft: if Hex is unreachable (no LAN / box down / no models), the test SKIPS so CI
without Hex stays green. It is a live integration probe, not a hermetic unit test.
"""

from __future__ import annotations

import os
import socket

import pytest

HEX_HOST = "10.0.0.100"
HEX_PORT = 11434
HEX_ENDPOINT = f"http://{HEX_HOST}:{HEX_PORT}"


def _hex_reachable() -> bool:
    try:
        with socket.create_connection((HEX_HOST, HEX_PORT), timeout=3):
            return True
    except OSError:
        return False


@pytest.mark.live
@pytest.mark.skipif(not _hex_reachable(), reason="Hex (10.0.0.100:11434) unreachable — live smoke skipped")
def test_dicksimnel_builds_on_hex(tmp_path, monkeypatch):
    """DS drives devstral@Hex through a real ToolLoop: tool_call → Write → DONE."""
    monkeypatch.setenv("INFERENCE_ENDPOINT", HEX_ENDPOINT)

    from unseen_university.devices.dicksimnel.device import DickSimnelDevice

    smoke_file = tmp_path / "SMOKE_OK.txt"
    hello_ticket = {
        "id": "T-ds-smoke-hello",
        "title": "Smoke: write a one-line scratch file",
        "tags": ["DickSimnel", "smoke"],
        "description": (
            f"Create the file {smoke_file} containing exactly one line:\n"
            "dicksimnel smoke ok\n\n"
            "Use the write tool with that absolute file_path and content. This is a "
            "trivial smoke test — one write tool call, then finish. When the file is "
            "written, output: DONE: wrote smoke file"
        ),
    }

    dev = DickSimnelDevice()
    result = dev._run_inference(hello_ticket)

    assert result is not None, "DS returned None — inference failed (Hex/routing/tool path)"
    assert result.strip().startswith("DONE:"), f"expected a DONE envelope, got: {result!r}"
    assert smoke_file.exists(), "the Write tool call did not create the smoke file"
    assert smoke_file.read_text().strip() == "dicksimnel smoke ok"
