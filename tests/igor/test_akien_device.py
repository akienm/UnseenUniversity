"""
test_akien_device.py — T-akien-rack-device

Tests: devices/akien/ importable; shim.who_am_i() returns correct shape.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import devices.akien as akien_device
from devices.akien.shim import AkienShim, who_am_i


class TestAkienDeviceImport:
    def test_package_importable(self):
        assert akien_device is not None

    def test_who_am_i_exported_from_package(self):
        assert callable(akien_device.who_am_i)

    def test_akien_shim_exported_from_package(self):
        assert akien_device.AkienShim is AkienShim


class TestAkienShimIdentity:
    def setup_method(self):
        self.shim = AkienShim()
        self.identity = self.shim.who_am_i()

    def test_returns_dict(self):
        assert isinstance(self.identity, dict)

    def test_id_is_akien(self):
        assert self.identity["id"] == "akien"

    def test_entity_type_is_human(self):
        assert self.identity["entity_type"] == "human"

    def test_address_is_comms_akien(self):
        assert self.identity["address"] == "comms://akien/"

    def test_data_home_is_string(self):
        assert isinstance(self.identity["data_home"], str)
        assert "akien" in self.identity["data_home"]

    def test_channels_present(self):
        ch = self.identity["channels"]
        assert isinstance(ch, dict)
        assert ch["inbox"] == "comms://akien/inbox"
        assert ch["outbox"] == "comms://akien/outbox"
        assert ch["ideas"] == "comms://akien/ideas"

    def test_online_is_false(self):
        assert self.identity["online"] is False

    def test_returns_copy(self):
        a = self.shim.who_am_i()
        b = self.shim.who_am_i()
        a["id"] = "mutated"
        assert b["id"] == "akien"


class TestModuleLevelWhoAmI:
    def test_module_function_returns_same_shape(self):
        result = who_am_i()
        assert result["id"] == "akien"
        assert result["address"] == "comms://akien/"
        assert result["entity_type"] == "human"
