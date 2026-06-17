"""Tests for pre_inference_assemble.py — pre-sprint context assembler."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock


def _mock_cc_queue_output(tid: str, title: str, tags: list, description: str) -> str:
    d = {"id": tid, "title": title, "tags": tags, "description": description, "status": "sprint"}
    return json.dumps(d, indent=2)


def _make_ticket(
    tid="T-test-ticket",
    title="BaseDevice shim lifecycle fix",
    tags=None,
    description="Fix the BaseShim start() method.\n\n**Affected files:** devices/granny/shim.py\n**Scope:** just shim\n**Completion criteria:** tests pass",
):
    if tags is None:
        tags = ["Granny", "Infrastructure"]
    return tid, title, tags, description


class TestExtractAffectedFiles:
    def test_single_file(self):
        from devlab.claudecode.pre_inference_assemble import _extract_affected_files

        desc = "Fix stuff.\n\n**Affected files:** devices/granny/shim.py\n**Design rules:** none"
        result = _extract_affected_files(desc)
        assert result == ["devices/granny/shim.py"]

    def test_multiple_files_comma_separated(self):
        from devlab.claudecode.pre_inference_assemble import _extract_affected_files

        desc = "**Affected files:** devices/granny/shim.py, devices/granny/daemon.py\n**Scope:** ..."
        result = _extract_affected_files(desc)
        assert "devices/granny/shim.py" in result
        assert "devices/granny/daemon.py" in result

    def test_tbd_excluded(self):
        from devlab.claudecode.pre_inference_assemble import _extract_affected_files

        desc = "**Affected files:** TBD — discovery step\n**Scope:** ..."
        result = _extract_affected_files(desc)
        assert result == []

    def test_parenthetical_notes_stripped(self):
        from devlab.claudecode.pre_inference_assemble import _extract_affected_files

        desc = "**Affected files:** devices/granny/shim.py (new file)\n**Scope:** ..."
        result = _extract_affected_files(desc)
        assert result == ["devices/granny/shim.py"]

    def test_no_affected_files_section(self):
        from devlab.claudecode.pre_inference_assemble import _extract_affected_files

        assert _extract_affected_files("Just some description.") == []


class TestLoadPatterns:
    def test_loads_patterns_from_doc(self, tmp_path, monkeypatch):
        from devlab.claudecode import pre_inference_assemble

        doc = tmp_path / "design_patterns_inventory.md"
        doc.write_text(textwrap.dedent("""\
            # Design Patterns

            ## PATTERN-001: BaseDevice / BaseShim Lifecycle

            **Kind:** Structural
            **When to use:** Every rack-registered component.

            **Canonical examples:**
            - `devices/auditor/shim.py` — no-op shim
        """))
        monkeypatch.setattr(pre_inference_assemble, "_PATTERNS_DOC", doc)

        patterns = pre_inference_assemble._load_patterns()
        assert len(patterns) == 1
        assert patterns[0]["id"] == "PATTERN-001"
        assert "lifecycle" in patterns[0]["keywords"] or "baseshim" in patterns[0]["keywords"]

    def test_empty_when_doc_missing(self, tmp_path, monkeypatch):
        from devlab.claudecode import pre_inference_assemble

        monkeypatch.setattr(pre_inference_assemble, "_PATTERNS_DOC", tmp_path / "nope.md")
        assert pre_inference_assemble._load_patterns() == []


class TestMatchPatterns:
    def test_higher_overlap_ranks_first(self):
        from devlab.claudecode.pre_inference_assemble import _match_patterns

        patterns = [
            {"id": "P-001", "title": "Alpha", "keywords": {"daemon", "pid", "shim"}, "examples": [], "block": ""},
            {"id": "P-002", "title": "Beta", "keywords": {"lifecycle"}, "examples": [], "block": ""},
        ]
        ticket_kw = {"daemon", "pid", "shim", "lifecycle"}
        results = _match_patterns(patterns, ticket_kw)
        assert results[0][1]["id"] == "P-001"  # 3 overlaps beats 1
        assert results[0][0] == 3

    def test_no_overlap_excluded(self):
        from devlab.claudecode.pre_inference_assemble import _match_patterns

        patterns = [{"id": "P-001", "title": "X", "keywords": {"unrelated"}, "examples": [], "block": ""}]
        results = _match_patterns(patterns, {"completely", "different", "words"})
        assert results == []


class TestAssemble:
    def _mock_subprocess_run(self, cmd, **kwargs):
        """Mock subprocess.run for cc_queue.py and repo_map.py calls."""
        cmd_str = " ".join(str(c) for c in cmd)
        if "cc_queue" in cmd_str and "show" in cmd_str:
            out = _mock_cc_queue_output(
                "T-test-ticket",
                "GrannyShim PID daemon lifecycle fix",
                ["Granny", "Infrastructure"],
                "Fix start method.\n\n**Affected files:** devices/granny/shim.py\n**Scope:** shim only\n**Completion criteria:** tests pass",
            )
            return MagicMock(returncode=0, stdout=out, stderr="")
        if "repo_map" in cmd_str:
            return MagicMock(returncode=0, stdout="devices/granny/shim.py\n  class GrannyShim", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    def test_produces_non_empty_output(self, tmp_path, monkeypatch):
        from devlab.claudecode import pre_inference_assemble

        # Point to real patterns doc
        real_doc = Path(__file__).parents[1] / "docs" / "design_patterns_inventory.md"
        if real_doc.exists():
            monkeypatch.setattr(pre_inference_assemble, "_PATTERNS_DOC", real_doc)

        with patch("devlab.claudecode.pre_inference_assemble.subprocess.run", side_effect=self._mock_subprocess_run):
            ctx = pre_inference_assemble.assemble("T-test-ticket")

        assert ctx["ticket_id"] == "T-test-ticket"
        assert ctx["affected_files"] == ["devices/granny/shim.py"]
        assert len(ctx["matched_patterns"]) > 0

    def test_json_output_is_valid(self, tmp_path, monkeypatch):
        from devlab.claudecode import pre_inference_assemble

        with patch("devlab.claudecode.pre_inference_assemble.subprocess.run", side_effect=self._mock_subprocess_run):
            ctx = pre_inference_assemble.assemble("T-test-ticket")

        # Verify JSON-serializable
        dumped = json.dumps(ctx)
        loaded = json.loads(dumped)
        assert loaded["ticket_id"] == "T-test-ticket"

    def test_format_text_contains_key_sections(self, monkeypatch):
        from devlab.claudecode.pre_inference_assemble import _format_text

        ctx = {
            "ticket_id": "T-foo",
            "title": "Test ticket",
            "tags": ["Infra"],
            "affected_files": ["devices/granny/shim.py"],
            "domain_keywords": ["daemon", "pid"],
            "matched_patterns": [
                {"id": "PATTERN-007", "title": "PID-File Daemon Management",
                 "overlap_score": 3, "examples": ["devices/granny/shim.py"]}
            ],
            "symbol_map": "class GrannyShim",
        }
        text = _format_text(ctx)
        assert "T-foo" in text
        assert "PATTERN-007" in text
        assert "AFFECTED FILES" in text
        assert "FILE SYMBOL MAP" in text
