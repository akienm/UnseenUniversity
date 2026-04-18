"""
test_matter_shelf.py — GH-185: Matter/home automation

Tests for Matter UC rack shelf.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_device(device_id="light-001", name="Office Light", device_type=None):
    from lab.utility_closet.matter_shelf import DeviceType, MatterDevice

    return MatterDevice(
        device_id=device_id,
        name=name,
        device_type=device_type or DeviceType.LIGHT,
        location="office",
        capabilities=["on_off", "brightness"],
    )


class TestMatterDevice:
    def test_to_dict(self):
        dev = _make_device()
        d = dev.to_dict()
        assert d["device_id"] == "light-001"
        assert d["device_type"] == "light"
        assert d["state"] == "unknown"
        assert "on_off" in d["capabilities"]


class TestMockController:
    def test_add_and_discover(self):
        from lab.utility_closet.matter_shelf import MockController

        ctrl = MockController()
        dev = _make_device()
        ctrl.add_device(dev)

        devices = ctrl.discover()
        assert len(devices) == 1
        assert devices[0].device_id == "light-001"

    def test_get_device(self):
        from lab.utility_closet.matter_shelf import MockController

        ctrl = MockController()
        dev = _make_device()
        ctrl.add_device(dev)

        found = ctrl.get_device("light-001")
        assert found is not None
        assert found.name == "Office Light"

        assert ctrl.get_device("nonexistent") is None

    def test_set_attribute(self):
        from lab.utility_closet.matter_shelf import MockController

        ctrl = MockController()
        dev = _make_device()
        ctrl.add_device(dev)

        assert ctrl.set_attribute("light-001", "brightness", 75) is True
        assert dev.attributes["brightness"] == 75

        assert ctrl.set_attribute("nonexistent", "x", 1) is False


class TestMatterShelf:
    def test_health(self):
        from lab.utility_closet.matter_shelf import MatterShelf

        shelf = MatterShelf()
        health = shelf.health()
        assert health["online"] is False
        assert health["devices"] == 0
        assert health["controller"] == "MockController"

    def test_start_stop(self):
        from lab.utility_closet.matter_shelf import MatterShelf

        shelf = MatterShelf()
        shelf.start()
        assert shelf.health()["online"] is True
        shelf.stop()
        assert shelf.health()["online"] is False

    def test_discover_devices(self):
        from lab.utility_closet.matter_shelf import MatterShelf, MockController

        ctrl = MockController()
        ctrl.add_device(_make_device("light-001", "Office Light"))
        ctrl.add_device(_make_device("sensor-001", "Temp Sensor"))

        shelf = MatterShelf(controller=ctrl)
        devices = shelf.discover_devices()
        assert len(devices) == 2
        assert shelf.list_devices() == devices

    def test_get_device(self):
        from lab.utility_closet.matter_shelf import MatterShelf, MockController

        ctrl = MockController()
        ctrl.add_device(_make_device())
        shelf = MatterShelf(controller=ctrl)
        shelf.discover_devices()

        dev = shelf.get_device("light-001")
        assert dev is not None
        assert dev.name == "Office Light"

        assert shelf.get_device("nonexistent") is None

    def test_set_attribute(self):
        from lab.utility_closet.matter_shelf import MatterShelf, MockController

        ctrl = MockController()
        ctrl.add_device(_make_device())
        shelf = MatterShelf(controller=ctrl)
        shelf.discover_devices()

        result = shelf.set_attribute("light-001", "on", True)
        assert result["success"] is True
        assert shelf.get_device("light-001").attributes["on"] is True

    def test_set_attribute_unknown_device(self):
        from lab.utility_closet.matter_shelf import MatterShelf

        shelf = MatterShelf()
        result = shelf.set_attribute("nonexistent", "on", True)
        assert "error" in result

    def test_register_as_sensors(self):
        from lab.utility_closet.matter_shelf import MatterShelf, MockController

        ctrl = MockController()
        ctrl.add_device(_make_device())
        shelf = MatterShelf(controller=ctrl)
        shelf.discover_devices()

        cortex = MagicMock()
        cortex.get.return_value = MagicMock()  # sensor root exists

        results = shelf.register_as_sensors(cortex)
        assert len(results) == 1
        assert results[0]["watch_type"] == "matter_device"
        cortex.store.assert_called()

    def test_module_metadata(self):
        from lab.utility_closet.matter_shelf import MatterShelf

        shelf = MatterShelf()
        assert shelf.name == "matter"
        assert shelf.module_type == "automation"
