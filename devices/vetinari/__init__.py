"""
Vetinari device - the meta-orchestrator for the agent collective.
"""
from typing import Any, Dict, List
from devices.base import BaseDevice
from devices.vetinari.vetinari import Vetinari


class VetinariDevice(BaseDevice):
    """Vetinari device implementation."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.vetinari = Vetinari(config)

    def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming data and make decisions."""
        return self.vetinari.process(data)

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of Vetinari."""
        return self.vetinari.get_status()
