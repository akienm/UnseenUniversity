"""Regression GUARD for aider clone-test import isolation (T-aider-real-uu-ticket-proof).

NOT a red->green proof — it documents a FALSIFIED premise. The ticket feared that
aider's gate would fake-green by importing the ORIGINAL working tree instead of the
clone's edits, because setuptools' editable install registers a MetaPathFinder that
redirects `unseen_university.*` to the original absolute path.

Empirically that does NOT happen for the real `_run_tests` path: it runs
`python -m pytest` with cwd=clone, which puts the clone on sys.path ahead of the
editable finder, so the clone's edits win — verified across shallow, deep, and
namespace-mapped modules on a full clone under the repo's --import-mode=importlib.
(The finder only wins under a bare `python -c` import, which is not how the gate
runs.) See the falsified-premise note filed with this ticket.

This guard LOCKS that invariant: if a future change to `_run_tests` (e.g. dropping
cwd=clone, or switching invocation) reintroduced the fake-green, the isolation
assertion below would fail. It passes on the current code by design.
"""

import textwrap
from pathlib import Path

from unseen_university.devices.aider.runner import _run_tests

_SENTINEL = "CLONE_SENTINEL_42"


def _make_clone(tmp_path: Path) -> Path:
    """A minimal clone that edits an EXISTING submodule (identity.py exists in the
    original tree) to a sentinel the installed original never returns. If imports
    leaked to the original tree, the probe below would not see the sentinel."""
    clone = tmp_path / "clone"
    pkg = clone / "unseen_university"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "identity.py").write_text(
        f"def instance_id():\n    return {_SENTINEL!r}\n"
    )
    tests = clone / "tests"
    tests.mkdir()
    (tests / "test_probe.py").write_text(
        textwrap.dedent(
            f"""
            from unseen_university.identity import instance_id


            def test_clone_wins():
                assert instance_id() == {_SENTINEL!r}
            """
        )
    )
    return clone


def test_fixture_discriminates():
    """Sanity: the installed original's instance_id() is NOT the sentinel, so the
    probe genuinely distinguishes clone-resolved from original-resolved imports.
    If this ever fails, the guard below would be meaningless."""
    from unseen_university.identity import instance_id as original

    assert original() != _SENTINEL


def test_run_tests_imports_clone_not_original_tree(tmp_path):
    """The invariant: _run_tests imports the CLONE's edits, not the original tree.
    A clone that edits identity.py to the sentinel and asserts it must report green;
    a regression that leaked to the original tree would report not-green."""
    clone = _make_clone(tmp_path)
    green, tail = _run_tests(clone, ["tests/test_probe.py"])
    assert green is True, f"isolation regressed — clone edit not seen; tail={tail!r}"


def test_no_target_returns_none_not_false(tmp_path):
    """No test target -> None (correctness not asserted, caller escalates), never a
    silent green/red."""
    green, _tail = _run_tests(tmp_path, [])
    assert green is None
