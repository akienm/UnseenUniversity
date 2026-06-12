"""
classifier/shim.py — Lifecycle management for the Classifier device.

Classifier is stateless beyond the meta-classifier rules — start/stop are lightweight.
self_test() verifies: device instantiates, classify() returns valid BuilderReport.
"""

from __future__ import annotations

import logging

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)


class ClassifierShim(BaseShim):
    def __init__(self, device=None) -> None:
        from devices.classifier.device import ClassifierDevice
        self._device = device or ClassifierDevice(llm_fallback=False)
        self._started = False

    @property
    def device_id(self) -> str:
        return "classifier"

    def start(self) -> bool:
        self._started = True
        log.info("classifier: started")
        return True

    def stop(self) -> bool:
        self._started = False
        log.info("classifier: stopped")
        return True

    def restart(self) -> bool:
        self.stop()
        self._device.restart()
        return self.start()

    def self_test(self) -> dict:
        """Verify: device instantiates, classify() returns valid BuilderReport."""
        failures = []

        try:
            from devices.classifier.device import ClassifierDevice
            device = ClassifierDevice(llm_fallback=False)
            report = device.classify("implement a new rack device with BaseDevice lifecycle", "unseen_university")
            if not hasattr(report, "relevant_files"):
                failures.append("classify() did not return a BuilderReport")
            if not isinstance(report.relevant_files, list):
                failures.append("BuilderReport.relevant_files is not a list")
            if not report.task_shape:
                failures.append("BuilderReport.task_shape is empty")
        except Exception as exc:
            failures.append(f"classify() raised: {exc}")

        try:
            from devices.classifier.report import BuilderReport
            report = BuilderReport(task_shape="codebase", confidence=0.9, classifier="meta_classifier")
            refreshed = device.freshness_check(report)
            if refreshed.stale:
                failures.append("freshness_check() incorrectly marked fresh report as stale")
        except Exception as exc:
            failures.append(f"freshness_check() raised: {exc}")

        if failures:
            return {"passed": False, "details": "; ".join(failures)}
        return {"passed": True, "details": "classify() and freshness_check() both ok"}

    def rollback(self) -> None:
        pass
