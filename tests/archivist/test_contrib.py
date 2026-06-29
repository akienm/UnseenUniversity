"""Tests for devices.archivist.contrib — global KB contribution pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ── context stripping ─────────────────────────────────────────────────────────


class TestStripContext:
    def _strip(self, text):
        from unseen_university.devices.archivist.contrib import strip_context
        return strip_context(text)

    def test_strips_home_path(self):
        result = self._strip("Use ~/dev/src/UnseenUniversity/config")
        assert "~/" not in result
        assert "<home-path>" in result or "<instance-path>" in result

    def test_strips_absolute_home(self):
        result = self._strip("Path: /home/akien/TheIgors/lab")
        assert "/home/akien" not in result

    def test_strips_instance_id(self):
        result = self._strip("Running on Igor-Wild1")
        assert "Igor-Wild1" not in result
        assert "<instance-id>" in result

    def test_strips_ticket_id(self):
        result = self._strip("See T-my-ticket-id for context")
        assert "T-my-ticket-id" not in result
        assert "<ticket-id>" in result

    def test_strips_decision_id(self):
        result = self._strip("Decision D-my-decision-2026-06-01 applies")
        assert "D-my-decision-2026-06-01" not in result

    def test_preserves_general_content(self):
        text = "Every device must inherit from BaseDevice and implement start/stop."
        assert self._strip(text) == text

    def test_strips_email(self):
        result = self._strip("Contact user@example.com for details")
        assert "user@example.com" not in result
        assert "<email>" in result


# ── staging ───────────────────────────────────────────────────────────────────


class TestStageCandidate:
    def _make_row(self, hash_suffix="abcdef01", hits=7, pattern="Use BaseDevice for all devices"):
        return {
            "pattern_hash": hash_suffix * 4,
            "pattern_text": pattern,
            "response_text": "Inherit from BaseDevice; implement start/stop/restart.",
            "hit_count": hits,
            "created_at": "2026-06-02T00:00:00Z",
            "last_hit_at": "2026-06-02T12:00:00Z",
        }

    def test_stages_record_to_file(self, tmp_path):
        from unseen_university.devices.archivist.contrib import stage_candidate

        row = self._make_row()
        record = stage_candidate(row, staging_dir=tmp_path)
        assert record is not None
        assert record["id"].startswith("PC-")
        # File written
        staged_files = list(tmp_path.glob("PC-*.json"))
        assert len(staged_files) == 1
        saved = json.loads(staged_files[0].read_text())
        assert saved["id"] == record["id"]

    def test_idempotent_skips_already_staged(self, tmp_path):
        from unseen_university.devices.archivist.contrib import stage_candidate

        row = self._make_row()
        r1 = stage_candidate(row, staging_dir=tmp_path)
        r2 = stage_candidate(row, staging_dir=tmp_path)
        assert r1 is not None
        assert r2 is None  # already staged

    def test_strips_instance_content(self, tmp_path):
        from unseen_university.devices.archivist.contrib import stage_candidate

        row = self._make_row(pattern="Path ~/TheIgors/lab used for T-my-ticket")
        record = stage_candidate(row, staging_dir=tmp_path)
        assert record is not None
        assert "~/TheIgors" not in record["content"]
        assert "T-my-ticket" not in record["content"]

    def test_staged_has_no_credentials(self, tmp_path):
        from unseen_university.devices.archivist.contrib import stage_candidate

        row = self._make_row(pattern="Normal pattern text without secrets")
        record = stage_candidate(row, staging_dir=tmp_path)
        assert record is not None
        # No api_key= or password= patterns in staged content
        content = record.get("content", "")
        assert "api_key=" not in content.lower()


# ── list_staged ───────────────────────────────────────────────────────────────


class TestListStaged:
    def test_returns_empty_when_missing(self, tmp_path):
        from unseen_university.devices.archivist.contrib import list_staged

        result = list_staged(staging_dir=tmp_path / "nonexistent")
        assert result == []

    def test_returns_all_staged(self, tmp_path):
        from unseen_university.devices.archivist.contrib import list_staged, stage_candidate

        for i in range(3):
            row = {
                "pattern_hash": f"abcd{i:04d}" * 4,
                "pattern_text": f"Pattern {i}",
                "response_text": f"Response {i}",
                "hit_count": 5 + i,
            }
            stage_candidate(row, staging_dir=tmp_path)

        records = list_staged(staging_dir=tmp_path)
        assert len(records) == 3


# ── build_pr_diff ──────────────────────────────────────────────────────────────


class TestBuildPrDiff:
    def test_contains_jsonl_line(self):
        from unseen_university.devices.archivist.contrib import build_pr_diff

        record = {
            "id": "PC-abcdef01",
            "title": "Use BaseDevice",
            "type": "pattern",
            "tags": ["Archivist"],
            "content": "Always inherit from BaseDevice.",
            "version": "1.0",
            "origin_hit_count": 9,
        }
        diff = build_pr_diff(record)
        assert "PC-abcdef01" in diff
        assert "proposed.jsonl" in diff
        # The JSONL line must be parseable
        import re
        match = re.search(r"```\n({.*})\n```", diff)
        assert match, "no JSONL line in diff"
        obj = json.loads(match.group(1))
        assert obj["id"] == "PC-abcdef01"
        assert obj["source"] == "unseen-university-kb"
        assert obj["origin_instance"] is None

    def test_strips_origin_fields(self):
        from unseen_university.devices.archivist.contrib import build_pr_diff

        record = {
            "id": "PC-test0001",
            "title": "Test",
            "type": "pattern",
            "tags": [],
            "content": "content",
            "version": "1.0",
            "staged_at": "2026-06-02T00:00:00Z",
            "origin_hit_count": 5,
        }
        diff = build_pr_diff(record)
        assert "staged_at" not in diff
        assert "origin_hit_count" not in diff or "origin_hit_count=5" in diff


# ── CONTRIB_CANDIDATE channel post ─────────────────────────────────────────────


class TestPostContribCandidate:
    def test_posts_to_channel(self):
        from unseen_university.devices.archivist.contrib import post_contrib_candidate

        posted = []
        with patch("unseen_university.channel.post_to_channel", side_effect=lambda *a, **kw: posted.append((a, kw))):
            post_contrib_candidate({"id": "PC-abc", "title": "Test", "origin_hit_count": 5})
        assert len(posted) == 1
        msg = posted[0][0][0]
        assert "CONTRIB_CANDIDATE" in msg
        assert "PC-abc" in msg

    def test_channel_failure_is_non_fatal(self):
        from unseen_university.devices.archivist.contrib import post_contrib_candidate

        with patch("unseen_university.channel.post_to_channel", side_effect=Exception("channel down")):
            # Must not raise
            post_contrib_candidate({"id": "PC-x", "title": "t", "origin_hit_count": 1})
