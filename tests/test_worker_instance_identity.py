"""
T-worker-instance-identity — the instance-address surface (D-worker-instance-identity-2026-07-02).

A worker addresses itself by INSTANCE (instance_name = "<abbrev>.<number>" -> "DS.0"), never by
its class/device id ("dicksimnel"). These tests pin: the mixin computes the address, DickSimnel
composes it, and _run_inference now hands run_capability the instance_name (was the literal
'dicksimnel'). The last is the behavioural proof node a hollow build could not pass.
"""

from __future__ import annotations

from unittest.mock import patch


def test_identity_mixin_composes_abbrev_and_number():
    """The bare surface: abbrev + number -> 'DS.0' (number defaults to 0, the foreground instance)."""
    from unseen_university.capabilities import IdentityMixin

    class Host(IdentityMixin):
        instance_abbreviation = "DS"

    assert Host().instance_name == "DS.0"


def test_identity_mixin_number_is_reflected():
    """instance_number is not hard-wired to 0 — a leased number (downstream) flows into the address."""
    from unseen_university.capabilities import IdentityMixin

    class Host(IdentityMixin):
        instance_abbreviation = "DS"
        instance_number = 3

    assert Host().instance_name == "DS.3"


def test_identity_mixin_defines_no_init():
    """MRO-transparency: the mixin adds no __init__, so it can never drop a device's
    construction step when composed (same contract the capability mixins hold)."""
    from unseen_university.capabilities import IdentityMixin

    assert "__init__" not in IdentityMixin.__dict__


def test_dicksimnel_instance_name_is_ds_zero():
    """DickSimnel sets instance_abbreviation='DS' -> its instance address is 'DS.0'."""
    from unseen_university.devices.dicksimnel.device import DickSimnelDevice

    dev = DickSimnelDevice.__new__(DickSimnelDevice)
    assert dev.instance_name == "DS.0"


def test_run_inference_uses_instance_name_not_class_id():
    """PROOF NODE: _run_inference addresses run_capability by the INSTANCE (agent_id='DS.0'),
    not by the class id ('dicksimnel'). A hollow build that left the literal 'dicksimnel' fails
    this exactly — the assertion is on the address the mixin computes, wired into the crossing."""
    from unseen_university.devices.dicksimnel.device import DickSimnelDevice

    dev = DickSimnelDevice.__new__(DickSimnelDevice)
    ticket = {"id": "T-id", "description": "d", "tags": []}
    with patch("unseen_university.capabilities.base.CapabilityMixin.run_capability") as spy:
        spy.return_value = "DONE: worked"
        out = dev._run_inference(ticket)

    spy.assert_called_once_with(ticket, agent_id="DS.0")
    assert out == "DONE: worked"
