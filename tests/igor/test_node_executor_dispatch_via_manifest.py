"""T-announce-slice4b: MCPCALL consults DatacenterClient manifest before
dispatching via the local tool registry.

Slice 4b is a resolution checkpoint, not a transport swap. When dc_client
is wired and its manifest names the requested tool, MCPCALL logs the
manifest binding (so future bus-routing slices have the hook), then
dispatches via the local registry as before. When dc_client is None or
the manifest doesn't list the tool, execution falls through to the
existing registry path with no manifest-bound log line.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition import node_executor
from unseen_university.devices.igor.cognition.node_executor import execute_node


def _memory_with_mcpcall(tool_name: str = "fake_tool") -> SimpleNamespace:
    """Build a minimal Memory-shaped object with one MCPCALL instruction."""
    return SimpleNamespace(
        id="mem-test",
        metadata={"triggers": {"__entry__": "cell"}},
        payload={
            "cell": [
                ["MCPCALL", tool_name, "args", "result"],
            ]
        },
    )


@pytest.fixture
def fake_tool(monkeypatch):
    """Register a synchronous fake tool in the registry under 'fake_tool'."""
    tool_obj = MagicMock()
    tool_obj.fn = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(
        node_executor._tool_registry,
        "get",
        lambda name: tool_obj if name == "fake_tool" else None,
    )
    return tool_obj


def test_mcpcall_consults_manifest_when_dc_client_present(fake_tool, caplog):
    """When dc_client.get_tool returns a binding, MCPCALL logs manifest-bound."""
    binding = SimpleNamespace(name="fake_tool", address="comms://igor.0.fake_tool")
    dc_client = MagicMock()
    dc_client.get_tool.return_value = binding

    memory = _memory_with_mcpcall("fake_tool")
    basket = {"args": {}}

    with caplog.at_level(logging.INFO, logger="unseen_university.devices.igor.cognition.node_executor"):
        execute_node(memory, "__entry__", basket, dc_client=dc_client)

    dc_client.get_tool.assert_called_once_with("fake_tool")
    assert any("manifest-bound tool: fake_tool" in r.message for r in caplog.records)
    assert basket["result"] == {"ok": True}
    fake_tool.fn.assert_called_once()


def test_mcpcall_falls_back_when_dc_client_missing(fake_tool, caplog):
    """When dc_client is None, no manifest log is emitted; registry serves directly."""
    memory = _memory_with_mcpcall("fake_tool")
    basket = {"args": {}}

    with caplog.at_level(logging.INFO, logger="unseen_university.devices.igor.cognition.node_executor"):
        execute_node(memory, "__entry__", basket, dc_client=None)

    assert not any("manifest-bound tool" in r.message for r in caplog.records)
    assert basket["result"] == {"ok": True}
    fake_tool.fn.assert_called_once()


def test_mcpcall_consults_manifest_but_falls_through_when_tool_unlisted(
    fake_tool, caplog
):
    """When dc_client.get_tool returns None, no manifest log; registry still serves."""
    dc_client = MagicMock()
    dc_client.get_tool.return_value = None

    memory = _memory_with_mcpcall("fake_tool")
    basket = {"args": {}}

    with caplog.at_level(logging.INFO, logger="unseen_university.devices.igor.cognition.node_executor"):
        execute_node(memory, "__entry__", basket, dc_client=dc_client)

    dc_client.get_tool.assert_called_once_with("fake_tool")
    assert not any("manifest-bound tool" in r.message for r in caplog.records)
    assert basket["result"] == {"ok": True}


def test_mcpcall_handles_dc_client_get_tool_raising(fake_tool, caplog):
    """A misbehaving dc_client must not break tool dispatch — registry still serves."""
    dc_client = MagicMock()
    dc_client.get_tool.side_effect = RuntimeError("boom")

    memory = _memory_with_mcpcall("fake_tool")
    basket = {"args": {}}

    with caplog.at_level(
        logging.WARNING, logger="unseen_university.devices.igor.cognition.node_executor"
    ):
        execute_node(memory, "__entry__", basket, dc_client=dc_client)

    assert any(
        "dc_client.get_tool" in r.message and "raised" in r.message
        for r in caplog.records
    )
    assert basket["result"] == {"ok": True}
