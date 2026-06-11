"""
test_memory_scope.py — Tests for #123: instance vs class memory split.

Verifies MemoryScope enum, default_scope(), Memory.scope field,
store/retrieve round-trip through Cortex, and get_portable().
Uses a temporary SQLite Cortex — no Postgres, no Ollama needed.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path


from devices.igor.memory.models import Memory, MemoryType, MemoryScope, BASE_CONFIDENCE, SOURCE_MULTIPLIER, default_scope


def _make_cortex(db_path: str):
    from devices.igor.memory.cortex import Cortex
    return Cortex(Path(db_path))


class TestMemoryScopeEnum(unittest.TestCase):

    def test_enum_values_exist(self):
        self.assertEqual(MemoryScope.CLASS.value, "class")
        self.assertEqual(MemoryScope.INSTANCE.value, "instance")
        self.assertEqual(MemoryScope.SESSION.value, "session")

    def test_default_scope_class_types(self):
        for mt in (
            MemoryType.ROOT, MemoryType.CORE_PATTERN, MemoryType.IDENTITY,
            MemoryType.ROLE_MODEL, MemoryType.PROCEDURAL, MemoryType.INTERPRETIVE,
            MemoryType.FACTUAL, MemoryType.REFERENCE, MemoryType.LEVER,
        ):
            self.assertEqual(default_scope(mt), MemoryScope.CLASS, f"Expected CLASS for {mt}")

    def test_default_scope_instance_types(self):
        for mt in (MemoryType.EPISODIC, MemoryType.EXPERIENTIAL, MemoryType.CREDENTIAL_REF):
            self.assertEqual(default_scope(mt), MemoryScope.INSTANCE, f"Expected INSTANCE for {mt}")


class TestMemoryScopeDataclass(unittest.TestCase):

    def test_factual_defaults_to_class(self):
        m = Memory("some fact", MemoryType.FACTUAL)
        self.assertEqual(m.scope, MemoryScope.CLASS)

    def test_episodic_defaults_to_instance(self):
        m = Memory("something happened", MemoryType.EPISODIC)
        self.assertEqual(m.scope, MemoryScope.INSTANCE)

    def test_experiential_defaults_to_instance(self):
        m = Memory("felt something", MemoryType.EXPERIENTIAL)
        self.assertEqual(m.scope, MemoryScope.INSTANCE)

    def test_credential_ref_defaults_to_instance(self):
        m = Memory("cred ref", MemoryType.CREDENTIAL_REF)
        self.assertEqual(m.scope, MemoryScope.INSTANCE)

    def test_explicit_scope_override(self):
        m = Memory("override", MemoryType.FACTUAL, scope=MemoryScope.SESSION)
        self.assertEqual(m.scope, MemoryScope.SESSION)


class TestMemoryScopeCortex(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path = self._tmp.name
        self._tmp.close()
        os.environ["IGOR_DB_PATH"] = self._db_path
        self._cortex = _make_cortex(self._db_path)

    def tearDown(self):
        os.unlink(self._db_path)
        if "IGOR_DB_PATH" in os.environ:
            del os.environ["IGOR_DB_PATH"]

    def test_scope_round_trips_class(self):
        m = Memory("a factual thing", MemoryType.FACTUAL)
        self.assertEqual(m.scope, MemoryScope.CLASS)
        self._cortex.store(m)
        retrieved = self._cortex.get(m.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.scope, MemoryScope.CLASS)

    def test_scope_round_trips_instance(self):
        m = Memory("an event", MemoryType.EPISODIC)
        self.assertEqual(m.scope, MemoryScope.INSTANCE)
        self._cortex.store(m)
        retrieved = self._cortex.get(m.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.scope, MemoryScope.INSTANCE)

    def test_scope_round_trips_session_override(self):
        m = Memory("session thing", MemoryType.FACTUAL, scope=MemoryScope.SESSION)
        self._cortex.store(m)
        retrieved = self._cortex.get(m.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.scope, MemoryScope.SESSION)

    def test_get_portable_returns_class_only(self):
        class_mem = Memory("shared knowledge", MemoryType.FACTUAL)
        instance_mem = Memory("my episode", MemoryType.EPISODIC)
        self._cortex.store(class_mem)
        self._cortex.store(instance_mem)
        portable = self._cortex.get_portable()
        ids = [m.id for m in portable]
        self.assertIn(class_mem.id, ids)
        self.assertNotIn(instance_mem.id, ids)

    def test_get_portable_excludes_session_scope(self):
        session_mem = Memory("ephemeral", MemoryType.FACTUAL, scope=MemoryScope.SESSION)
        self._cortex.store(session_mem)
        portable = self._cortex.get_portable()
        ids = [m.id for m in portable]
        self.assertNotIn(session_mem.id, ids)

    def test_procedural_is_class_scoped(self):
        m = Memory("how to do X", MemoryType.PROCEDURAL)
        self._cortex.store(m)
        retrieved = self._cortex.get(m.id)
        self.assertEqual(retrieved.scope, MemoryScope.CLASS)

    def test_credential_ref_not_in_portable(self):
        cred = Memory("where api key lives", MemoryType.CREDENTIAL_REF)
        self._cortex.store(cred)
        portable = self._cortex.get_portable()
        ids = [m.id for m in portable]
        self.assertNotIn(cred.id, ids)


class TestEpistemicSourceMultiplier(unittest.TestCase):

    def test_experimental_higher_than_read(self):
        e = Memory("I tested this", MemoryType.FACTUAL, epistemic_source="experimental")
        r = Memory("I read this", MemoryType.FACTUAL, epistemic_source="read")
        self.assertGreater(e.confidence, r.confidence)

    def test_derived_higher_than_observed(self):
        d = Memory("I reasoned this", MemoryType.FACTUAL, epistemic_source="derived")
        o = Memory("I saw this once", MemoryType.FACTUAL, epistemic_source="observed")
        self.assertGreater(d.confidence, o.confidence)

    def test_unknown_is_default(self):
        m = Memory("no source info", MemoryType.FACTUAL)
        self.assertEqual(m.epistemic_source, "unknown")

    def test_multiplier_ordering(self):
        sources = ["experimental", "derived", "unknown", "read", "observed"]
        prev = float("inf")
        for src in sources:
            m = Memory("test", MemoryType.FACTUAL, epistemic_source=src)
            self.assertLessEqual(m.confidence, prev, f"{src} should be <= prior")
            prev = m.confidence

    def test_source_multiplier_keys(self):
        for key in ["experimental", "derived", "unknown", "read", "observed"]:
            self.assertIn(key, SOURCE_MULTIPLIER)

    def test_cortex_roundtrip_preserves_epistemic_source(self):
        import os
        import tempfile
        from pathlib import Path
        from devices.igor.memory.cortex import Cortex
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            os.environ["IGOR_DB_PATH"] = db_path
            cortex = Cortex(Path(db_path))
            m = Memory("I personally verified this", MemoryType.FACTUAL, epistemic_source="experimental")
            cortex.store(m)
            loaded = cortex.get(m.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.epistemic_source, "experimental")
        finally:
            os.unlink(db_path)
            os.environ.pop("IGOR_DB_PATH", None)


class TestLeverMemoryType(unittest.TestCase):

    def test_lever_enum_importable(self):
        self.assertEqual(MemoryType.LEVER.value, "LEVER")

    def test_lever_base_confidence(self):
        self.assertEqual(BASE_CONFIDENCE[MemoryType.LEVER], 0.85)

    def test_lever_memory_confidence_property(self):
        m = Memory("physics intuition applies to software architecture", MemoryType.LEVER)
        # 0.85 base * 0.75 unknown-source multiplier
        self.assertGreaterEqual(m.confidence, 0.85 * 0.75)

    def test_lever_experimental_source_hits_full_base(self):
        m = Memory("I proved this cross-domain pattern myself", MemoryType.LEVER, epistemic_source="experimental")
        self.assertGreaterEqual(m.confidence, 0.85)

    def test_lever_defaults_to_class_scope(self):
        m = Memory("cross-domain pattern", MemoryType.LEVER)
        self.assertEqual(m.scope, MemoryScope.CLASS)


if __name__ == "__main__":
    unittest.main()
