"""T-igor-inference-bypassers: igor's /relay + /cloud reach providers only via the Proxy.

The survey found direct-reasoner / raw-provider call sites OUTSIDE the gateway:
relay.py (self.reasoner.reason), multi_cloud.py (reasoner.reason + raw ollama.chat
in compare_responses), and main.py _cloud_query (r.reason). Per Akien's contract:
code asks for a tier; specific-model is allowed ONLY for testing / experiments /
comparison, and even those go THROUGH the Inference Proxy (req.model) — never a raw
reasoner or raw provider client. What is pinned:

  * relay.py / multi_cloud.py contain no raw reasoner.reason() or raw ollama/urllib.
  * RelaySession.send dispatches through the Proxy with the requested model.
  * query_multiple dispatches each model through the Proxy (comparison exception).
  * compare_responses synthesizes through the Proxy (tier-routed), not raw ollama.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from unseen_university._uu_root import uu_root
from unseen_university.devices.igor.cognition.multi_cloud import (
    compare_responses,
    query_multiple,
)
from unseen_university.devices.igor.cognition.relay import RelaySession
from unseen_university.devices.inference.shim import InferenceResponse

_COG = Path(uu_root()) / "unseen_university/devices/igor/cognition"
# Raw-provider / direct-reasoner tokens that must not survive in these files.
_FORBIDDEN = [r"\.reason\(", r"import ollama", r"_ollama", r"urllib"]


def _spy(text="ok", cost=0.0):
    spy = MagicMock()
    spy.dispatch.return_value = InferenceResponse(
        text=text, cost_estimate=cost, source_kind="cloud"
    )
    return spy


def test_no_raw_provider_calls_in_relay_and_multicloud():
    """PROOF NODE. relay.py + multi_cloud.py must reach inference only via the Proxy.

    Signature-stable grep gate: pre-impl these files held self.reasoner.reason(),
    reasoner.reason(), and a raw `import ollama` / `_ollama.chat` — so the forbidden
    tokens are present and the assertion fails cleanly (AssertionError, authentic red
    for proof_emitter). Post-impl they dispatch through the Proxy and the tokens are
    gone -> green. No construction, so no collateral ERROR.
    """
    hits = []
    for fname in ("relay.py", "multi_cloud.py"):
        text = (_COG / fname).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            for pat in _FORBIDDEN:
                if re.search(pat, line):
                    hits.append(f"{fname}:{i}: {line.strip()[:90]}")
    assert not hits, "raw provider/reasoner calls survive:\n" + "\n".join(hits)


def test_relay_send_dispatches_through_proxy_with_model():
    spy = _spy(text="relay reply")
    sess = RelaySession(model_name="anthropic/claude-haiku-4.5", inference=spy)

    out = sess.send("hello")

    spy.dispatch.assert_called_once()
    req = spy.dispatch.call_args.args[0]
    assert req.model == "anthropic/claude-haiku-4.5"
    assert out == "relay reply"


def test_query_multiple_dispatches_each_model_through_proxy():
    spy = _spy(text="answer", cost=0.01)
    results = query_multiple(
        "compare this",
        {"haiku": "anthropic/claude-haiku-4.5", "deepseek": "deepseek/deepseek-v4-flash"},
        inference=spy,
    )

    assert spy.dispatch.call_count == 2
    dispatched_models = {c.args[0].model for c in spy.dispatch.call_args_list}
    assert dispatched_models == {
        "anthropic/claude-haiku-4.5",
        "deepseek/deepseek-v4-flash",
    }
    assert [r[0] for r in results] == ["haiku", "deepseek"]
    assert all(r[1] == "answer" and r[2] == 0.01 for r in results)


def test_compare_responses_synthesizes_through_proxy():
    spy = _spy(text="they mostly agree")
    out = compare_responses(
        [("haiku", "the sky is blue", 0.0), ("deepseek", "sky=blue", 0.0)],
        inference=spy,
    )

    spy.dispatch.assert_called_once()
    # synthesis dispatch asks for a tier, not a specific model
    assert spy.dispatch.call_args.args[0].model == ""
    assert "they mostly agree" in out
