"""tests/test_web_chat_swadl_smoke.py — SWADL smoke test for chat reply.

Uses SWADLBaseAutomation (not SWADLTest) because this test is really
operational automation surfacing as a test — there's no per-test cross-
cutting state to track. Imports of SWADL.* stay deferred to inside the
test function as a defensive habit; lazy driver creation (T-swadl-base-
automation-split) means imports no longer auto-spawn browsers, but the
deferred-import pattern documents the historical trap at the boundary
where readers see it.

Cleanup is explicit via the SWADLBaseAutomation context manager; the
browser quits even on assertion failure.
"""

import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path.home() / "TheIgors"))


def is_chat_server_up():
    """Probe localhost:8080 with a real HTTP request — TCP-connect alone is
    insufficient because other services may squat the port without speaking
    HTTP. Any 2xx/3xx/4xx response counts as reachable; 5xx, timeouts,
    and connection failures all skip."""
    try:
        req = urllib.request.Request("http://localhost:8080/", method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


@pytest.mark.skipif(
    not is_chat_server_up(),
    reason="localhost:8080 chat server unreachable — skipping SWADL smoke",
)
def test_web_chat_reply_surfacing():
    """Send message, assert non-empty Igor reply within 120s."""
    from SWADL.engine.swadl_base_automation import SWADLBaseAutomation

    from wild_igor.tools.swadl_flows.web_chat import WebChatFlow

    class WebChatAutomation(SWADLBaseAutomation):
        pass

    with WebChatAutomation():
        flow = WebChatFlow()
        reply = flow.send_message_and_read_reply(
            "Hello Igor, are you there?", timeout_sec=120
        )
        assert reply, "No reply received from Igor within timeout"
