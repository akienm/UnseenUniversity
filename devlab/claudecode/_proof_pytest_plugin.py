"""Pytest plugin that captures per-test outcome + exception class for the
proof emitter's red/green authentication.

Loaded by proof_emitter via ``-p _proof_pytest_plugin`` in a subprocess. It
writes one JSON object to the path in ``$PROOF_RUN_OUT`` at session finish:

    {"exit": <int>, "reports": [
        {"nodeid": "...", "outcome": "passed|failed", "exc_type": "AssertionError"|null},
        ...
    ], "collect_errors": ["<path>", ...]}

Why the exception *class* and not the failure message: pytest's assertion
rewriting makes a failed ``assert x == y`` report its crash message as the
assert expression ("assert None == 5"), NOT the string "AssertionError" — so
matching message text silently misclassifies. ``call.excinfo.type.__name__``
from the makereport hookwrapper is the reliable signal. Verified empirically
by the proof_emitter test suite (test_*_classified_as_*).
"""
import json
import os

import pytest

_REPORTS = []
_COLLECT_ERRORS = []
# nodeid -> exception class name, captured at call time from excinfo.
_EXC_BY_NODE = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and call.excinfo is not None:
        _EXC_BY_NODE[report.nodeid] = call.excinfo.type.__name__


def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    _REPORTS.append({
        "nodeid": report.nodeid,
        "outcome": report.outcome,
        "exc_type": _EXC_BY_NODE.get(report.nodeid) if report.failed else None,
    })


def pytest_collectreport(report):
    if report.failed:
        _COLLECT_ERRORS.append(str(getattr(report, "fspath", "") or report.nodeid))


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("PROOF_RUN_OUT")
    if not out:
        return
    payload = {
        "exit": int(exitstatus),
        "reports": _REPORTS,
        "collect_errors": _COLLECT_ERRORS,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
