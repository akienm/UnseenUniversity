"""
Slice 4 — system_prompt CAPABILITIES layer tests.

Verifies build_system_prompt(datacenter_client=...):
  - No client: existing behavior preserved (no CAPABILITIES section, no etag in cache)
  - Client present but manifest=None: same as no client (graceful)
  - Client + manifest: CAPABILITIES section present, etag in cache key
  - Cache key changes when profile_etag changes (prompt rebuilds)
  - Capability layer character cap honored
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from unseen_university.devices.igor.cognition import system_prompt as sp


def _fake_memory(narr, mem_type, inertia=0.5):
    m = MagicMock()
    m.id = f"M{abs(hash(narr)) % 10000}"
    m.narrative = narr
    m.memory_type = mem_type
    m.inertia = inertia
    return m


def _fake_cortex():
    """Minimal cortex stub returning enough memories to build a real prompt."""
    from unseen_university.devices.igor.memory.models import MemoryType

    cortex = MagicMock()

    def get_by_type(mt, limit=None, order_by=None):
        if mt == MemoryType.CORE_PATTERN:
            return [
                _fake_memory("CP1: world is not safe", mt, inertia=0.95),
                _fake_memory("CP2: name what you see", mt, inertia=0.95),
            ]
        if mt == MemoryType.IDENTITY:
            return [_fake_memory("ID1: I am Igor", mt, inertia=0.9)]
        if mt == MemoryType.PROCEDURAL:
            return [_fake_memory("P1: read first", mt, inertia=0.5)]
        return []

    cortex.get_by_type.side_effect = get_by_type
    return cortex


def _client_with_manifest(profile_etag="aaa", tools=None, state_refs=None):
    """Build a stub DatacenterClient holding a synthetic manifest."""
    from unseen_university.announce import DatacenterClient, IdentityEnvelope

    identity = IdentityEnvelope(
        agent_id="igor",
        instance="wild-test",
        box="testhost",
        box_n=0,
        pid=1,
        interface_version="1.0",
    )
    client = DatacenterClient(
        agent_id=identity.agent_id,
        instance=identity.instance,
        box=identity.box,
        box_n=identity.box_n,
        pid=identity.pid,
    )
    default_tools = [
        {
            "name": "inference",
            "address": "comms://inference",
            "interface": "imap_envelope",
            "input_schema": {},
            "output_schema": None,
            "permission_mode": "read_write",
            "rate_limit_per_min": 60,
            "description": "",
        },
        {
            "name": "postgres",
            "address": "comms://postgres",
            "interface": "imap_envelope",
            "input_schema": {},
            "output_schema": None,
            "permission_mode": "read_write",
            "rate_limit_per_min": None,
            "description": "",
        },
    ]
    default_refs = [
        {"name": "twm", "uri": "postgres://...#twm", "mode": "read_write"},
        {"name": "ne", "uri": "postgres://...#ne", "mode": "read_write"},
    ]
    client._manifest = {
        "tools": default_tools if tools is None else tools,
        "state_refs": default_refs if state_refs is None else state_refs,
        "subscriptions": [],
        "acl": {
            "inbound_allow": ["*"],
            "inbound_deny": [],
            "outbound_allow": ["*"],
            "outbound_deny": [],
        },
        "surface_addresses": {},
        "primary_address": "comms://testhost.0",
        "profile_etag": profile_etag,
    }
    return client


class TestSystemPromptCapabilityLayer(unittest.TestCase):
    def setUp(self):
        sp.invalidate_cache()

    def tearDown(self):
        sp.invalidate_cache()

    def test_prompt_without_client_unchanged(self):
        cortex = _fake_cortex()
        prompt = sp.build_system_prompt(cortex, instance_id="wild-test")
        self.assertNotIn("DATACENTER CAPABILITIES", prompt)
        self.assertIn("Igor", prompt)

    def test_prompt_with_client_no_manifest_unchanged(self):
        cortex = _fake_cortex()
        client = MagicMock()
        client.manifest = None
        prompt = sp.build_system_prompt(
            cortex, instance_id="wild-test", datacenter_client=client
        )
        self.assertNotIn("DATACENTER CAPABILITIES", prompt)

    def test_prompt_with_manifest_includes_capabilities(self):
        cortex = _fake_cortex()
        client = _client_with_manifest()
        prompt = sp.build_system_prompt(
            cortex, instance_id="wild-test", datacenter_client=client
        )
        self.assertIn("DATACENTER CAPABILITIES", prompt)
        self.assertIn("inference", prompt)
        self.assertIn("postgres", prompt)
        self.assertIn("twm", prompt)
        self.assertIn("ne", prompt)

    def test_cache_key_includes_profile_etag(self):
        cortex = _fake_cortex()
        client_a = _client_with_manifest(profile_etag="etag-aaa")
        client_b = _client_with_manifest(profile_etag="etag-bbb")

        prompt_a = sp.build_system_prompt(
            cortex, instance_id="wild-test", datacenter_client=client_a
        )
        prompt_b = sp.build_system_prompt(
            cortex, instance_id="wild-test", datacenter_client=client_b
        )
        # Same content but different etags → cache must rebuild → distinct
        # cache entries. The body (which prepends CAPABILITIES from the same
        # manifest) is identical, so we verify cache distinctness by counting:
        cached_keys = set(sp._cache.keys())
        self.assertEqual(len(cached_keys), 2, "etag should yield distinct cache key")
        self.assertEqual(prompt_a, prompt_b)  # bodies match (same tools/refs)

    def test_capability_layer_character_cap_honored(self):
        cortex = _fake_cortex()
        # 30 long-named tools → joined list well over 200 chars
        many_tools = [
            {
                "name": f"some_long_tool_name_{i:02d}",
                "address": f"comms://tool{i}",
                "interface": "imap_envelope",
                "input_schema": {},
                "output_schema": None,
                "permission_mode": "read_write",
                "rate_limit_per_min": None,
                "description": "",
            }
            for i in range(30)
        ]
        client = _client_with_manifest(tools=many_tools)
        prompt = sp.build_system_prompt(
            cortex, instance_id="wild-test", datacenter_client=client
        )
        # Find the tools line and verify length cap
        for line in prompt.splitlines():
            if line.strip().startswith("tools:"):
                self.assertTrue(line.endswith("…"), "tools line should be truncated")
                self.assertLessEqual(
                    len(line.split(":", 1)[1].strip()),
                    sp._CAPABILITY_LAYER_MAX_CHARS,
                )
                break
        else:
            self.fail("no tools line found")

    def test_capability_layer_with_empty_manifest_omits_section(self):
        cortex = _fake_cortex()
        client = _client_with_manifest(tools=[], state_refs=[])
        prompt = sp.build_system_prompt(
            cortex, instance_id="wild-test", datacenter_client=client
        )
        self.assertNotIn("DATACENTER CAPABILITIES", prompt)


if __name__ == "__main__":
    unittest.main()
