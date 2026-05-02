"""
datacenter_mcp wrapper tests — verify MCP tool registration + dispatch
forwarding without booting a real stdio server.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Test mode must be set BEFORE bus.imap_server is imported by AnnounceMcpServer.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab" / "claudecode"))


class TestDatacenterMcpWrapper(unittest.TestCase):
    def setUp(self):
        # Fresh import per test so the singleton can be reset.
        import datacenter_mcp

        importlib.reload(datacenter_mcp)
        self.mod = datacenter_mcp
        self.mod._ADAPTER = None  # reset singleton

    def test_wrapper_imports_without_error(self):
        # Already imported in setUp — assert key surface exists.
        self.assertTrue(hasattr(self.mod, "server"))
        self.assertTrue(hasattr(self.mod, "list_tools"))
        self.assertTrue(hasattr(self.mod, "call_tool"))
        self.assertTrue(hasattr(self.mod, "_dispatch"))

    def test_wrapper_exposes_three_tools(self):
        tools = asyncio.run(self.mod.list_tools())
        names = {t.name for t in tools}
        self.assertEqual(
            names,
            {
                "datacenter_announce",
                "datacenter_manifest",
                "datacenter_check_for_invalidate",
            },
        )

    def test_announce_tool_dispatches_to_adapter(self):
        fake = MagicMock()
        fake.announce_tool.return_value = {"ok": True, "manifest": {"x": 1}}
        self.mod._ADAPTER = fake

        result = self.mod._dispatch("datacenter_announce", {"timeout": 1.5})
        self.assertEqual(result, {"ok": True, "manifest": {"x": 1}})
        fake.announce_tool.assert_called_once_with(timeout=1.5)

    def test_manifest_tool_dispatches_to_adapter(self):
        fake = MagicMock()
        fake.manifest_tool.return_value = {"ok": True, "manifest": None}
        self.mod._ADAPTER = fake

        result = self.mod._dispatch("datacenter_manifest", {})
        self.assertEqual(result, {"ok": True, "manifest": None})
        fake.manifest_tool.assert_called_once_with()

    def test_check_for_invalidate_tool_dispatches_to_adapter(self):
        fake = MagicMock()
        fake.check_for_invalidate_tool.return_value = {
            "ok": True,
            "handled": 0,
            "manifest": None,
        }
        self.mod._ADAPTER = fake

        result = self.mod._dispatch(
            "datacenter_check_for_invalidate", {"reannounce_timeout": 0.5}
        )
        self.assertEqual(result["handled"], 0)
        fake.check_for_invalidate_tool.assert_called_once_with(reannounce_timeout=0.5)

    def test_unknown_tool_returns_error_dict(self):
        result = self.mod._dispatch("not_a_real_tool", {})
        self.assertFalse(result["ok"])
        self.assertIn("unknown tool", result["error"])

    def test_call_tool_serializes_result_to_text_content(self):
        """The MCP entry point wraps the dict result as TextContent JSON."""
        fake = MagicMock()
        fake.manifest_tool.return_value = {"ok": True, "manifest": {"k": "v"}}
        self.mod._ADAPTER = fake

        content = asyncio.run(self.mod.call_tool("datacenter_manifest", {}))
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0].type, "text")
        parsed = json.loads(content[0].text)
        self.assertEqual(parsed, {"ok": True, "manifest": {"k": "v"}})


if __name__ == "__main__":
    unittest.main()
