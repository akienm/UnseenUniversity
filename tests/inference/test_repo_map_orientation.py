"""
Repo signature-map orientation proof (T-coding-repo-map-orientation).

D-coding-loop-redesign-aider-survey-2026-07-04. The coding loop's orientation used to
render a bare relevant-FILES list (one symbol per file — orientation_classifier dedups to
one FileMatch per path), so a weak model still had to open files to see structure and
read-wandered. aider's repo map instead shows the KEY SYMBOL SIGNATURES across the relevant
files, so the model can plan without reading bodies.

THE DISCRIMINATOR (advisor 2026-07-04): the old bare list already prints each file's summary,
which is signature-shaped — so "a signature is present" and "no file body" pass on BOTH the
old and new formats and prove nothing. The one property the old path STRUCTURALLY cannot
produce is MULTIPLE symbols for the same file (it keeps only the highest-scored per path). So
the proof anchors there: a file with two matching symbols S1 (high score) and S2 (low score) —
the signature map must surface S2; the old bare list drops it.

Revert-safe: drives the STABLE public surface CodingDomain()._initial_message(ticket) with
psycopg2.connect patched to a fake cursor (both the old query_relevant_files and the new
signature-map query issue the same SELECT and hit it). Names no impl-only symbol, so the
reverted run imports cleanly and reaches the assertion (AssertionError, not ImportError).
"""

from __future__ import annotations

from unittest.mock import patch

from unseen_university.devices.inference.domains.coding import CodingDomain


# A file (pkg/foo.py) with TWO keyword-matching symbols; the second is what the old
# one-symbol-per-file path cannot surface.
_ROWS = [
    ("pkg/foo.py", "parse_widget", "function", "def parse_widget(data: dict) — parse the widget"),
    ("pkg/foo.py", "helper_fn", "function", "def helper_fn(x) — supports the widget flow"),
    ("pkg/bar.py", "other_thing", "function", "def other_thing() — widget adjacent utility"),
]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        return None

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        return None


def _orientation(ticket) -> str:
    with patch("psycopg2.connect", lambda *a, **k: _FakeConn(_ROWS)):
        return CodingDomain(name="coding")._initial_message(ticket)


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


def test_signature_map_surfaces_multiple_symbols_per_file(tmp_path):
    """The orientation must surface a file's SECOND matching symbol's signature.

    GREEN (signature map): pkg/foo.py lists both parse_widget AND helper_fn, so the editor's
    architect can plan against the second symbol without opening the file. RED (old bare list):
    orientation_classifier dedups pkg/foo.py to its top symbol (parse_widget) and drops
    helper_fn → 'def helper_fn(x)' is absent → AssertionError. The second symbol is the whole
    proof; body-free/ranking are shared invariants and don't differentiate.
    """
    ticket = {
        "id": "T-map-proof",
        "title": "process the widget parser here",
        "tags": ["widget"],
        "description": "work on the widget parser flow",
    }
    orientation = _orientation(ticket)
    assert "def helper_fn(x)" in orientation, (
        "signature map must surface a file's SECOND matching symbol (helper_fn) — the bare "
        f"one-symbol-per-file list drops it. Orientation rendered:\n{orientation[:600]}"
    )
