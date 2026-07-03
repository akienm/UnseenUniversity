"""
identity — the worker instance-identity surface (address, not personality).

D-worker-instance-identity-2026-07-02. A worker addresses itself by its INSTANCE,
never by its class/device id ("address instances, never classes"). This mixin carries
the address half:

    instance_name = f"{instance_abbreviation}.{instance_number}"   ->  "DS.0"

`instance_abbreviation` is a class attr the device sets ("DS"). `instance_number`
is now a property that reads the UU_INSTANCE_NUMBER env var (default 0 = foreground/primary
instance). The REUSABLE-number lease from the shim front-door (D-shim-frontdoor-on-groundloop,
downstream ticket T-shim-lease-instance-numbers) sets this env var on spawn.

The PERSONALITY half ("who Dick Simnel is" — full name, character, aliases) is class-level
prompt content, not a device field, and lives in the shared base prompt — deliberately out
of this surface.

MRO-transparency: this mixin defines no __init__, so composing it onto a device leaves the
device's construction chain (super().__init__() -> BaseDevice) untouched — the same
transparency the capability mixins rely on (see CapabilityMixin).
"""

from __future__ import annotations

import os


class IdentityMixin:
    """Gives a host an instance address: ``instance_name == "<abbrev>.<number>"``.

    Compose alongside a capability mixin, e.g.
    ``class DickSimnelDevice(IdentityMixin, CodingCapability, BaseDevice)`` with
    ``instance_abbreviation = "DS"`` -> ``instance_name == "DS.0"``.
    """

    #: Class-level short address prefix the device sets, e.g. "DS".
    instance_abbreviation: str = ""

    @property
    def instance_number(self) -> int:
        """The instance's number from the UU_INSTANCE_NUMBER env var.

        Defaults to 0 (foreground/primary instance). The shim front-door sets this
        env var on spawn via the InstancePool lease system (D-worker-pool-leasing-and-singleton-2026-07-02).
        """
        return int(os.environ.get("UU_INSTANCE_NUMBER", "0"))

    @property
    def instance_name(self) -> str:
        """The canonical instance address, e.g. ``"DS.0"`` — this is the agent_id."""
        return f"{self.instance_abbreviation}.{self.instance_number}"
