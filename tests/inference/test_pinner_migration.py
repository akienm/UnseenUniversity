"""
Tests for T-inference-migrate-pinners: production callers route by {domain, task_class};
only the three sanctioned uses keep a model pin, each tagged with a pin_reason.

Static guarantee (criterion 1): grep the production device tree for any non-empty
`model=` InferenceRequest pin that is NOT accompanied by a sanctioned pin_reason.
"""

from __future__ import annotations

import re
from pathlib import Path

from unseen_university.devices.inference.shim import (
    SANCTIONED_PIN_REASONS,
    InferenceRequest,
)

_DEVICES = Path(__file__).resolve().parents[2] / "unseen_university" / "devices"

# Files with a legitimately pinned model — each must carry a sanctioned pin_reason.
_SANCTIONED_SITES = {
    "evaluator/device.py": "model_competition",   # model_eval_run — competition
    "inference/sources.py": "inference_test",      # self_test — testing the inference system
}


def test_default_model_is_empty_not_a_latent_pin():
    """The design-center default no longer pins a specific model."""
    assert InferenceRequest(messages=[]).model == ""


def _inference_request_spans(text: str):
    """Yield the argument text of each `InferenceRequest(...)` construction.

    Balanced-paren scan so only real InferenceRequest calls are inspected — not
    InferenceResponse records, ModelSelection defs, direct anthropic SDK calls, or
    variables whose name merely ends in 'model'.
    """
    for m in re.finditer(r"\bInferenceRequest\s*\(", text):
        i = m.end()
        depth = 1
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        yield text[m.end():i - 1]


def test_no_unsanctioned_model_pins_in_production_devices():
    """No production device constructs an InferenceRequest with a bare literal model pin.

    A migrated caller sets model='' (route by domain) or omits model entirely; the only
    surviving pins are the sanctioned sites, which sit next to a pin_reason in the SAME
    InferenceRequest construction.
    """
    offenders: list[str] = []
    for py in _DEVICES.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for span in _inference_request_spans(text):
            m = re.search(r'model\s*=\s*"([^"]+)"', span)
            if not m:
                continue  # model='' or model=<var pass-through> or omitted — fine
            if "pin_reason" in span:
                continue  # sanctioned pin (reason validated by the test below)
            rel = str(py.relative_to(_DEVICES.parent.parent))
            offenders.append(f"{rel}: model={m.group(1)!r} (no pin_reason)")
    assert not offenders, "unsanctioned InferenceRequest model pins remain:\n" + "\n".join(offenders)


def test_sanctioned_sites_carry_a_valid_pin_reason():
    """Each sanctioned pin site names a reason that is in SANCTIONED_PIN_REASONS."""
    for rel, expected in _SANCTIONED_SITES.items():
        text = (_DEVICES / rel).read_text(encoding="utf-8")
        assert f'pin_reason="{expected}"' in text, f"{rel} missing pin_reason={expected}"
        assert expected in SANCTIONED_PIN_REASONS
