"""
Tests for T-template-extractor-habit:
  - recognize_pattern
  - parameterize_template
  - seed_pattern_extractor_habits: PROC_PATTERN_RECOGNIZE, PROC_TEMPLATE_PARAMETERIZE,
    PROC_TEMPLATE_INSTANTIATE habit metadata correctness

Coverage:
  - recognize_pattern: empty input → ERROR
  - recognize_pattern: no templates in matrix → ERROR
  - recognize_pattern: LLM unavailable → ERROR
  - recognize_pattern: LLM returns valid JSON → parsed and returned
  - recognize_pattern: LLM returns bad JSON → ERROR
  - parameterize_template: empty input → ERROR
  - parameterize_template: empty pattern_name → ERROR
  - parameterize_template: pattern not in matrix → ERROR
  - parameterize_template: LLM returns valid JSON → returned with forced template_id
  - parameterize_template: LLM returns bad JSON → ERROR
  - habit metadata: PROC_PATTERN_RECOGNIZE has delegation type + code_ref
  - habit metadata: PROC_TEMPLATE_PARAMETERIZE has delegation type + code_ref
  - habit metadata: PROC_TEMPLATE_INSTANTIATE has delegation type + code_ref
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_template(template_id: str, pattern_name: str, slots: list) -> "Memory":
    from igor.memory.models import Memory, MemoryType

    m = Memory(
        id=template_id,
        narrative=f"{pattern_name} template",
        memory_type=MemoryType.PROCEDURAL,
    )
    m.metadata = {
        "template_schema": {
            "pattern_name": pattern_name,
            "schema_version": 1,
            "substitution_engine": "jinja2",
            "slot_manifest": slots,
            "expansion_schema": [{"id": "{{name}}", "narrative": "test"}],
            "instantiation_contract": {"produces": ["habit"], "edge_policy": "none"},
        }
    }
    return m


def _make_mock_cortex(templates: list = None):
    c = MagicMock()
    procs = templates or []
    c.get_by_type.return_value = procs
    return c


# ── recognize_pattern ─────────────────────────────────────────────────────────


class TestRecognizePatternEmpty(unittest.TestCase):
    def test_empty_input(self):
        from igor.tools.template_tools import recognize_pattern

        result = recognize_pattern("")
        self.assertTrue(result.startswith("ERROR:"))

    def test_whitespace_input(self):
        from igor.tools.template_tools import recognize_pattern

        result = recognize_pattern("   ")
        self.assertTrue(result.startswith("ERROR:"))


class TestRecognizePatternNoTemplates(unittest.TestCase):
    def test_no_templates_in_matrix(self):
        from igor.tools.template_tools import recognize_pattern

        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[])
            result = recognize_pattern("def run(): pass")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("no TEMPLATE", result)


class TestRecognizePatternLlmUnavailable(unittest.TestCase):
    def test_no_api_key(self):
        from igor.tools.template_tools import recognize_pattern

        tpl = _fake_template(
            "tpl-cached-probe",
            "CACHED_PROBE",
            [
                {"name": "probe_name", "required": True, "type_hint": "str"},
            ],
        )
        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[tpl])
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
                result = recognize_pattern("def run(): check_cache()")
        self.assertTrue(result.startswith("ERROR:"))


class TestRecognizePatternLlmSuccess(unittest.TestCase):
    def test_valid_json_response(self):
        from igor.tools.template_tools import recognize_pattern

        tpl = _fake_template(
            "tpl-cached-probe",
            "CACHED_PROBE",
            [
                {"name": "probe_name", "required": True, "type_hint": "str"},
            ],
        )
        llm_reply = json.dumps(
            {
                "pattern_name": "CACHED_PROBE",
                "template_id": "tpl-cached-probe",
                "confidence": 0.92,
                "reasoning": "Has cache-age check and stale-refresh branch.",
            }
        )
        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[tpl])
            with patch("igor.tools.template_tools._llm_call", return_value=llm_reply):
                result = recognize_pattern("def run(): if stale: refresh()")

        self.assertFalse(result.startswith("ERROR:"))
        parsed = json.loads(result)
        self.assertEqual(parsed["pattern_name"], "CACHED_PROBE")
        self.assertAlmostEqual(parsed["confidence"], 0.92)

    def test_bad_json_from_llm(self):
        from igor.tools.template_tools import recognize_pattern

        tpl = _fake_template("tpl-cached-probe", "CACHED_PROBE", [])
        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[tpl])
            with patch("igor.tools.template_tools._llm_call", return_value="not json"):
                result = recognize_pattern("def run(): pass")

        self.assertTrue(result.startswith("ERROR:"))

    def test_missing_key_in_response(self):
        from igor.tools.template_tools import recognize_pattern

        tpl = _fake_template("tpl-cached-probe", "CACHED_PROBE", [])
        llm_reply = json.dumps({"pattern_name": "CACHED_PROBE"})  # missing keys
        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[tpl])
            with patch("igor.tools.template_tools._llm_call", return_value=llm_reply):
                result = recognize_pattern("def run(): pass")

        self.assertTrue(result.startswith("ERROR:"))


# ── parameterize_template ─────────────────────────────────────────────────────


class TestParameterizeTemplateEmpty(unittest.TestCase):
    def test_empty_code(self):
        from igor.tools.template_tools import parameterize_template

        result = parameterize_template("", "CACHED_PROBE")
        self.assertTrue(result.startswith("ERROR:"))

    def test_empty_pattern_name(self):
        from igor.tools.template_tools import parameterize_template

        result = parameterize_template("def run(): pass", "")
        self.assertTrue(result.startswith("ERROR:"))


class TestParameterizeTemplateNotFound(unittest.TestCase):
    def test_unknown_pattern(self):
        from igor.tools.template_tools import parameterize_template

        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[])
            result = parameterize_template("def run(): pass", "NONEXISTENT_PATTERN")

        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("no TEMPLATE node found", result)


class TestParameterizeTemplateLlmSuccess(unittest.TestCase):
    def test_valid_extraction(self):
        from igor.tools.template_tools import parameterize_template

        tpl = _fake_template(
            "tpl-cached-probe",
            "CACHED_PROBE",
            [
                {"name": "probe_name", "required": True, "type_hint": "str"},
                {"name": "source_fn", "required": True, "type_hint": "str"},
                {
                    "name": "cache_ttl",
                    "required": False,
                    "type_hint": "int",
                    "default": 300,
                },
            ],
        )
        llm_reply = json.dumps(
            {
                "template_id": "tpl-cached-probe",
                "pattern_name": "CACHED_PROBE",
                "params": {
                    "probe_name": "disk-usage",
                    "source_fn": "check_disk_usage",
                    "cache_ttl": 300,
                },
                "missing": [],
            }
        )
        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[tpl])
            with patch("igor.tools.template_tools._llm_call", return_value=llm_reply):
                result = parameterize_template(
                    "def check(): if stale(disk_cache, 300): refresh()",
                    "CACHED_PROBE",
                )

        self.assertFalse(result.startswith("ERROR:"))
        parsed = json.loads(result)
        self.assertEqual(parsed["template_id"], "tpl-cached-probe")
        self.assertEqual(parsed["params"]["probe_name"], "disk-usage")
        self.assertEqual(parsed["missing"], [])

    def test_template_id_always_forced(self):
        """template_id in output is always the real node id, not whatever LLM says."""
        from igor.tools.template_tools import parameterize_template

        tpl = _fake_template(
            "tpl-cached-probe",
            "CACHED_PROBE",
            [
                {"name": "probe_name", "required": True, "type_hint": "str"},
            ],
        )
        llm_reply = json.dumps(
            {
                "template_id": "tpl-wrong-id",  # LLM hallucinated wrong id
                "pattern_name": "CACHED_PROBE",
                "params": {"probe_name": "disk-usage"},
                "missing": [],
            }
        )
        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[tpl])
            with patch("igor.tools.template_tools._llm_call", return_value=llm_reply):
                result = parameterize_template("code here", "CACHED_PROBE")

        parsed = json.loads(result)
        self.assertEqual(parsed["template_id"], "tpl-cached-probe")  # forced correct

    def test_bad_json_from_llm(self):
        from igor.tools.template_tools import parameterize_template

        tpl = _fake_template(
            "tpl-cached-probe",
            "CACHED_PROBE",
            [
                {"name": "probe_name", "required": True, "type_hint": "str"},
            ],
        )
        with patch("igor.tools.template_tools._get_cortex") as mock_ctx:
            mock_ctx.return_value = _make_mock_cortex(templates=[tpl])
            with patch("igor.tools.template_tools._llm_call", return_value="oops"):
                result = parameterize_template("code", "CACHED_PROBE")

        self.assertTrue(result.startswith("ERROR:"))


# ── habit metadata ─────────────────────────────────────────────────────────────


class TestPatternExtractorHabitMetadata(unittest.TestCase):
    """Verify seed habits have correct structure without DB access."""

    def _load_habits(self) -> dict:
        """Import seed script and return habits dict keyed by id."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "seed_pe",
            Path(__file__).parent.parent
            / "lab" / "claudecode"
            / "seed_pattern_extractor_habits.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return {h.id: h for h in mod.HABITS}

    def setUp(self):
        # Prevent actual DB access during module load
        with patch("wild_igor.igor.memory.cortex.Cortex"):
            self.habits = self._load_habits()

    def test_all_three_habits_present(self):
        for hid in (
            "PROC_PATTERN_RECOGNIZE",
            "PROC_TEMPLATE_PARAMETERIZE",
            "PROC_TEMPLATE_INSTANTIATE",
        ):
            self.assertIn(hid, self.habits, f"missing habit {hid}")

    def test_habits_are_delegation_type(self):
        for hid, h in self.habits.items():
            self.assertEqual(
                h.metadata.get("habit_type"),
                "delegation",
                f"{hid}: expected habit_type=delegation",
            )

    def test_habits_have_code_ref(self):
        expected = {
            "PROC_PATTERN_RECOGNIZE": "recognize_pattern",
            "PROC_TEMPLATE_PARAMETERIZE": "parameterize_template",
            "PROC_TEMPLATE_INSTANTIATE": "instantiate_template",
        }
        for hid, code_ref in expected.items():
            self.assertEqual(
                self.habits[hid].metadata.get("code_ref"),
                code_ref,
                f"{hid}: expected code_ref={code_ref}",
            )

    def test_habits_have_trigger(self):
        for hid, h in self.habits.items():
            self.assertIn("trigger", h.metadata, f"{hid}: missing trigger")
            self.assertTrue(h.metadata["trigger"], f"{hid}: trigger is empty")

    def test_habits_are_procedural(self):
        for hid, h in self.habits.items():
            self.assertEqual(
                h.memory_type.value,
                "PROCEDURAL",
                f"{hid}: expected PROCEDURAL memory_type",
            )

    def test_habits_have_template_tag(self):
        for hid, h in self.habits.items():
            tags = h.metadata.get("tags", [])
            self.assertIn("template", tags, f"{hid}: missing 'template' in tags")


# ── tool registration ─────────────────────────────────────────────────────────


class TestToolRegistration(unittest.TestCase):
    def test_recognize_pattern_registered(self):
        from igor.tools.registry import registry
        import igor.tools.template_tools  # noqa: F401 — ensure registration runs

        tool = registry.get("recognize_pattern")
        self.assertIsNotNone(tool, "recognize_pattern not in registry")

    def test_parameterize_template_registered(self):
        from igor.tools.registry import registry
        import igor.tools.template_tools  # noqa: F401

        tool = registry.get("parameterize_template")
        self.assertIsNotNone(tool, "parameterize_template not in registry")


if __name__ == "__main__":
    unittest.main()
