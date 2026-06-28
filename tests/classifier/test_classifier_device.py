"""
tests/classifier/test_classifier_device.py — Classifier device tests.

Coverage:
  - BuilderReport schema (round-trip)
  - meta_classifier rule-based routing
  - ClassifierDevice.classify() returns valid BuilderReport
  - ClassifierDevice.freshness_check() stale/fresh detection
  - ClassifierDevice.score() returns expected shape
  - ClassifierShim.self_test() passes
  - Lifecycle: start, self_test, stop — no exceptions
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from unseen_university.devices.classifier.report import BuilderReport
from unseen_university.devices.classifier.meta_classifier import classify_task
from unseen_university.devices.classifier.device import ClassifierDevice, STALE_THRESHOLD_SECONDS
from unseen_university.devices.classifier.shim import ClassifierShim


# ── BuilderReport ─────────────────────────────────────────────────────────────

class TestBuilderReport:
    def test_defaults(self):
        r = BuilderReport()
        assert r.relevant_files == []
        assert r.context_nodes == []
        assert r.task_shape == ""
        assert r.confidence == 0.0
        assert r.classifier == ""
        assert r.stale is False
        assert r.ts  # auto-set

    def test_round_trip(self):
        r = BuilderReport(
            relevant_files=["devices/foo/device.py"],
            context_nodes=["palace.codebase.unseen_university"],
            task_shape="codebase",
            confidence=0.9,
            classifier="meta_classifier",
            stale=False,
            ts="2026-06-12T10:00:00+00:00",
        )
        d = r.to_dict()
        r2 = BuilderReport.from_dict(d)
        assert r2.relevant_files == r.relevant_files
        assert r2.task_shape == r.task_shape
        assert r2.confidence == r.confidence
        assert r2.ts == r.ts

    def test_from_dict_empty(self):
        r = BuilderReport.from_dict({})
        assert r.relevant_files == []
        assert r.confidence == 0.0


# ── meta_classifier ───────────────────────────────────────────────────────────

class TestMetaClassifier:
    def test_codebase_routing(self):
        shape, trees, conf, name = classify_task(
            "refactor toolloop.py to split billing_type logic", "unseen_university"
        )
        assert shape == "codebase"
        assert any("unseen_university" in t for t in trees)
        assert conf > 0

    def test_cognition_routing(self):
        shape, trees, conf, name = classify_task(
            "fix hebbian attention weight update in memory palace", "unseen_university"
        )
        assert shape == "cognition"
        assert conf > 0

    def test_routing_routing(self):
        shape, trees, conf, name = classify_task(
            "granny tier cascade: dispatch idle master devices", "unseen_university"
        )
        assert shape == "routing"

    def test_infra_routing(self):
        shape, trees, conf, name = classify_task(
            "fix BaseShim start() lifecycle on bus announce", "unseen_university"
        )
        assert shape in ("infra", "codebase")  # infra and codebase rules can both match

    def test_meta_routing(self):
        shape, trees, conf, name = classify_task(
            "design decision: classifier device architecture phase 2", "unseen_university"
        )
        assert shape == "meta"

    def test_empty_task_returns_unknown(self):
        shape, trees, conf, name = classify_task("", "unseen_university")
        assert shape == "unknown"
        assert conf == 0.0

    def test_short_task_lower_confidence(self):
        shape, trees, conf, name = classify_task("fix bug", "unseen_university")
        # May not match any rule or match with low confidence
        assert conf <= 0.5

    def test_tree_cap_three(self):
        # Long description that matches many rules — tree count capped at 3
        desc = (
            "granny dispatch: consequence audit of BaseDevice cognition memory palace "
            "hebbian workflow migration test fix design decision"
        )
        _, trees, _, _ = classify_task(desc, "unseen_university")
        assert len(trees) <= 3

    def test_project_id_in_codebase_tree(self):
        shape, trees, conf, name = classify_task(
            "refactor device.py to extract interface", "my_project"
        )
        assert any("my_project" in t for t in trees)


# ── ClassifierDevice ──────────────────────────────────────────────────────────

class TestClassifierDevice:
    def setup_method(self):
        self.device = ClassifierDevice(llm_fallback=False)

    def test_classify_returns_builder_report(self):
        report = self.device.classify(
            "implement a new rack device with BaseDevice lifecycle", "unseen_university"
        )
        assert isinstance(report, BuilderReport)
        assert isinstance(report.relevant_files, list)
        assert report.task_shape
        assert report.ts

    def test_classify_sets_classifier_name(self):
        report = self.device.classify("fix hebbian memory in palace", "unseen_university")
        assert report.classifier

    def test_freshness_check_fresh(self):
        report = BuilderReport(
            task_shape="codebase",
            confidence=0.9,
            classifier="meta_classifier",
            ts=datetime.now(timezone.utc).isoformat(),
        )
        refreshed = self.device.freshness_check(report)
        assert refreshed.stale is False

    def test_freshness_check_stale(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD_SECONDS + 1)).isoformat()
        report = BuilderReport(
            task_shape="codebase",
            confidence=0.9,
            classifier="meta_classifier",
            ts=old_ts,
        )
        refreshed = self.device.freshness_check(report)
        assert refreshed.stale is True

    def test_freshness_check_empty_ts_is_stale(self):
        report = BuilderReport(task_shape="codebase", confidence=0.5, classifier="test", ts="")
        report.ts = ""  # override the auto-set ts
        refreshed = self.device.freshness_check(report)
        assert refreshed.stale is True

    def test_score_returns_dict_with_ticket_id(self):
        result = self.device.score("T-foo", ["devices/foo/device.py"])
        assert result["ticket_id"] == "T-foo"
        assert "precision" in result
        assert "recall" in result

    def test_health_ok(self):
        health = self.device.health()
        assert health["status"] == "ok"
        assert health["uptime"] >= 0


# ── Lifecycle ─────────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_lifecycle(self):
        """Start, self_test(), stop — no exceptions. Ticket completion criterion."""
        shim = ClassifierShim()
        assert shim.start() is True
        result = shim.self_test()
        assert result["passed"] is True, f"self_test failed: {result.get('details')}"
        assert shim.stop() is True

    def test_restart(self):
        shim = ClassifierShim()
        shim.start()
        assert shim.restart() is True
        assert shim.stop() is True
