"""
Tests for T-swarm-identity-layer.

Covers:
  - resolve(comms://CC.0) returns device record
  - resolve(box.cc.0) returns same record when box == local hostname
  - resolve(box.cc.0/console) returns record with surface="console"
  - resolve(box.igor.0) maps to igor-wild-0001
  - resolve on unknown address returns None (not crash)
  - resolve with cross-box address returns None
  - surface routing: /mcp, /inference, /console
  - _agent_mailbox mappings
  - _split_surface helper
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from unseen_university.config.device_config import DeviceConfig
from unseen_university.devices.skeleton.registry import DeviceRegistry, _agent_mailbox, _split_surface

# ── helpers ───────────────────────────────────────────────────────────────────


def _reg(tmp_path: Path) -> DeviceRegistry:
    return DeviceRegistry(path=tmp_path / "devices.json")


def _register_cc(reg: DeviceRegistry, n: int = 0) -> None:
    reg.register(
        device_id=f"CC.{n}",
        config=DeviceConfig(),
        mailbox=f"comms://CC.{n}",
        name="Claude Code",
    )


def _register_igor(reg: DeviceRegistry, n: int = 0) -> None:
    instance = f"wild-{n + 1:04d}"
    reg.register(
        device_id=f"igor-{instance}",
        config=DeviceConfig(),
        mailbox=f"comms://igor-{instance}",
        name="Igor",
    )


_LOCAL = socket.gethostname()


# ── comms:// form ─────────────────────────────────────────────────────────────


def test_resolve_comms_returns_device_record(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_cc(reg)
    record = reg.resolve("comms://CC.0")
    assert record is not None
    assert record["mailbox"] == "comms://CC.0"


def test_resolve_comms_unknown_returns_none(tmp_path: Path):
    reg = _reg(tmp_path)
    assert reg.resolve("comms://nonexistent") is None


# ── box-qualified form ────────────────────────────────────────────────────────


def test_resolve_box_qualified_cc_matches_comms_form(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_cc(reg)

    via_comms = reg.resolve("comms://CC.0")
    via_box = reg.resolve(f"{_LOCAL}.cc.0")

    assert via_box is not None
    assert via_box["mailbox"] == via_comms["mailbox"]
    assert via_box["id"] == via_comms["id"]


def test_resolve_box_qualified_igor_finds_igor_record(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_igor(reg, n=0)

    record = reg.resolve(f"{_LOCAL}.igor.0")
    assert record is not None
    assert record["mailbox"] == "comms://igor-wild-0001"


def test_resolve_cross_box_returns_none(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_cc(reg)
    assert reg.resolve("akiendell.cc.0") is None  # different box


def test_resolve_malformed_path_returns_none(tmp_path: Path):
    reg = _reg(tmp_path)
    assert reg.resolve("notabox") is None
    assert reg.resolve("only.two") is None
    assert reg.resolve("too.many.segments.here") is None


def test_resolve_non_integer_n_returns_none(tmp_path: Path):
    reg = _reg(tmp_path)
    assert reg.resolve(f"{_LOCAL}.cc.abc") is None


def test_resolve_empty_registry_returns_none(tmp_path: Path):
    reg = _reg(tmp_path)
    assert reg.resolve(f"{_LOCAL}.cc.0") is None


# ── surface suffix ────────────────────────────────────────────────────────────


def test_resolve_with_console_surface(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_cc(reg)
    record = reg.resolve(f"{_LOCAL}.cc.0/console")
    assert record is not None
    assert record["surface"] == "console"
    assert record["mailbox"] == "comms://CC.0"


def test_resolve_with_mcp_surface(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_cc(reg)
    record = reg.resolve(f"{_LOCAL}.cc.0/mcp")
    assert record["surface"] == "mcp"


def test_resolve_with_inference_surface(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_cc(reg)
    record = reg.resolve(f"{_LOCAL}.cc.0/inference")
    assert record["surface"] == "inference"


def test_resolve_comms_form_has_no_surface_key(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_cc(reg)
    record = reg.resolve("comms://CC.0")
    assert "surface" not in record


def test_resolve_without_surface_has_no_surface_key(tmp_path: Path):
    reg = _reg(tmp_path)
    _register_cc(reg)
    record = reg.resolve(f"{_LOCAL}.cc.0")
    assert "surface" not in record


# ── pass condition: box.cc.0/console == comms://CC.0 on local box ─────────────


def test_pass_condition_box_qualified_same_device_as_comms(tmp_path: Path):
    """
    Ticket pass condition (adapted for local hostname):
    registry.resolve("<local>.cc.0/console") resolves to the same device
    as registry.resolve("comms://CC.0") on a box where CC.0 is registered.
    """
    reg = _reg(tmp_path)
    _register_cc(reg)

    via_comms = reg.resolve("comms://CC.0")
    via_box = reg.resolve(f"{_LOCAL}.cc.0/console")

    assert via_comms is not None
    assert via_box is not None
    assert via_box["id"] == via_comms["id"]
    assert via_box["mailbox"] == via_comms["mailbox"]
    assert via_box["surface"] == "console"


# ── _agent_mailbox mappings ───────────────────────────────────────────────────


def test_agent_mailbox_cc():
    assert _agent_mailbox("cc", 0) == "comms://CC.0"
    assert _agent_mailbox("cc", 1) == "comms://CC.1"


def test_agent_mailbox_igor():
    assert _agent_mailbox("igor", 0) == "comms://igor-wild-0001"
    assert _agent_mailbox("igor", 1) == "comms://igor-wild-0002"


def test_agent_mailbox_skeleton():
    assert _agent_mailbox("skeleton", 0) == "comms://skeleton"
    assert _agent_mailbox("skeleton", 1) == "comms://skeleton"  # singleton


def test_agent_mailbox_passthrough():
    assert _agent_mailbox("inference", 0) == "comms://inference.0"
    assert _agent_mailbox("postgres", 0) == "comms://postgres.0"


# ── _split_surface helper ─────────────────────────────────────────────────────


def test_split_surface_with_surface():
    path, surf = _split_surface("akiendell.cc.0/console")
    assert path == "akiendell.cc.0"
    assert surf == "console"


def test_split_surface_without_surface():
    path, surf = _split_surface("akiendell.cc.0")
    assert path == "akiendell.cc.0"
    assert surf is None


def test_split_surface_empty_surface_treated_as_none():
    path, surf = _split_surface("akiendell.cc.0/")
    assert path == "akiendell.cc.0"
    assert surf is None
