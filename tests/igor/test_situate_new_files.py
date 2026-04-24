"""T-situate-accepts-declared-new-files — Affected-files-field accepts
declared new paths, not just existing ones.

Before this fix: _affected_files_from_description reused _parse_file_list
which filtered out non-existent paths. Every new-file Igor ticket (e.g.
T-no-sqlite-enforcement declaring 'new lab/claudecode/check_no_sqlite.py')
got plan_files=[] at SITUATE, fell through to tier.2, and often hallucinated
brainstem/core_patterns.py — triggering the stuck-ticket chain.
"""

from __future__ import annotations

import pytest

from wild_igor.igor.tools.pe_chain import (
    _affected_files_from_description,
    _affected_files_from_description_detailed,
    _parse_declared_file_list,
    _parse_file_list,
)


class TestParseDeclared:
    def test_accepts_existing_path(self):
        all_paths, new_paths = _parse_declared_file_list(
            "wild_igor/igor/tools/pe_chain.py"
        )
        assert "wild_igor/igor/tools/pe_chain.py" in all_paths
        assert "wild_igor/igor/tools/pe_chain.py" not in new_paths

    def test_accepts_new_path(self):
        all_paths, new_paths = _parse_declared_file_list(
            "lab/claudecode/brand_new_never_existed.py"
        )
        assert "lab/claudecode/brand_new_never_existed.py" in all_paths
        assert "lab/claudecode/brand_new_never_existed.py" in new_paths

    def test_mix_existing_and_new(self):
        raw = "wild_igor/igor/tools/pe_chain.py\n" "lab/claudecode/never_existed_xyz.py"
        all_paths, new_paths = _parse_declared_file_list(raw)
        assert len(all_paths) == 2
        assert new_paths == ["lab/claudecode/never_existed_xyz.py"]

    def test_comma_separated(self):
        all_paths, _ = _parse_declared_file_list(
            "wild_igor/igor/tools/pe_chain.py\nlab/claudecode/never_xyz.py"
        )
        assert len(all_paths) == 2


class TestStrictParseStillExistsOnly:
    """_parse_file_list is the tier.2-output guard — must stay strict."""

    def test_strict_drops_nonexistent(self):
        assert _parse_file_list("lab/claudecode/never_existed_xyz.py") == []

    def test_strict_keeps_existing(self):
        paths = _parse_file_list("wild_igor/igor/tools/pe_chain.py")
        assert "wild_igor/igor/tools/pe_chain.py" in paths


class TestAffectedFilesFromDescription:
    def test_accepts_declared_new_path(self):
        desc = "**Affected files:** lab/claudecode/proposed_new_file.py"
        paths = _affected_files_from_description(desc)
        assert paths == ["lab/claudecode/proposed_new_file.py"]

    def test_detailed_marks_new_paths(self):
        desc = (
            "**Affected files:** "
            "wild_igor/igor/tools/pe_chain.py, "
            "lab/claudecode/proposed_new_file.py"
        )
        all_paths, new_paths = _affected_files_from_description_detailed(desc)
        assert "wild_igor/igor/tools/pe_chain.py" in all_paths
        assert "lab/claudecode/proposed_new_file.py" in all_paths
        assert new_paths == ["lab/claudecode/proposed_new_file.py"]

    def test_tbd_still_returns_empty(self):
        assert _affected_files_from_description("**Affected files:** TBD") == []

    def test_empty_description_still_empty(self):
        assert _affected_files_from_description("") == []

    def test_no_field_still_empty(self):
        assert (
            _affected_files_from_description("body with no affected files field") == []
        )


class TestSituateIntegration:
    def test_situate_writes_new_files_marker(self):
        from wild_igor.igor.tools.pe_chain import pe_situate

        basket = {
            "ticket_id": "T-test-new-file",
            "ticket_description": (
                "Build a thing.\n"
                "**Affected files:** lab/claudecode/proposed_never_existed.py"
            ),
        }
        result = pe_situate(basket)
        assert result["plan_files"] == ["lab/claudecode/proposed_never_existed.py"]
        assert result["situate_source"] == "affected_files_field"
        assert result.get("new_files") == ["lab/claudecode/proposed_never_existed.py"]

    def test_situate_no_marker_when_paths_exist(self):
        from wild_igor.igor.tools.pe_chain import pe_situate

        basket = {
            "ticket_id": "T-test-existing",
            "ticket_description": (
                "Body.\n**Affected files:** wild_igor/igor/tools/pe_chain.py"
            ),
        }
        result = pe_situate(basket)
        assert result["plan_files"] == ["wild_igor/igor/tools/pe_chain.py"]
        assert "new_files" not in result or result["new_files"] == []
