"""
Repo signature-map orientation proof (T-coding-repo-map-orientation, T-graph-map-ingests-venvs).

D-coding-loop-redesign-aider-survey-2026-07-04. The coding loop's orientation used to render a
bare relevant-FILES list (one symbol per file — orientation_classifier dedups to one FileMatch
per path), so a weak model still had to open files to see structure and read-wandered. aider's
repo map instead shows the KEY SYMBOLS across the relevant files, so the model can plan without
reading bodies.

THE DISCRIMINATOR (advisor 2026-07-04): the old bare list already prints each file's summary,
which is signature-shaped — so "a signature is present" and "no file body" pass on BOTH the old
and new formats and prove nothing. The one property the old path STRUCTURALLY cannot produce is
MULTIPLE symbols for the same file (it keeps only the highest-scored per path). So the proof
anchors there: a file with two matching symbols, both of which must surface.

Why this test was rewritten (2026-07-09)
----------------------------------------
The original drove `CodingDomain()._initial_message(ticket)` with `psycopg2.connect` patched to
a fake cursor, asserting a fabricated symbol `helper_fn` came back from the symbols table. That
fixture went DEAD without failing honestly: `build_signature_map` now tries `build_graph_map`
FIRST and returns early whenever it yields a packet (orientation_classifier.py:298-303). The
graph map is a stdlib-`ast` walk of the real repo and has no DB dependency, so the patched
cursor was never consulted and the assertion asked for a symbol that exists in no file on disk.
The red meant "the fixture no longer reaches the code", not "the property broke" — the worst
kind of red, because it looks like a regression and cannot be made green honestly.

So the proof now drives `build_graph_map` directly against a HERMETIC tmp repo (it takes a
`repo_root`), and pins the two properties that matter, one of which was genuinely broken:

  1. multi-symbol — `pkg/foo.py` surfaces BOTH of its matching symbols.
  2. no vendored code — a virtualenv inside the repo contributes NOTHING to the map.

(2) was live and wrong. `_gather_py_files` skipped directories by NAME (`.venv`, `venv`, …)
while its docstring claimed it skipped "hidden/venv/site dirs". This repo's gitignored
virtualenv is called `test_env/` — 2173 Python files — so every DickSimnel orientation ranked
pip's vendored `typing_extensions` above the actual task files. A weak model told those are the
most task-relevant files in the repo is being actively misdirected.

Hermetic: builds its own tiny repo in tmp_path. No DB, no device, no network, no live tree.
"""

from __future__ import annotations

from pathlib import Path

from unseen_university.devices.scraps.repo_graph_map import build_graph_map

_TICKET = {
    "id": "T-map-proof",
    "title": "process the widget parser here",
    "tags": ["widget"],
    "description": "work on the widget parser flow",
}


def _make_repo(root: Path) -> Path:
    """A tiny repo: two real modules, plus a virtualenv with a NON-STANDARD name.

    `test_env` is the exact name of this repo's own gitignored venv — the one a name-blacklist
    skip list misses. Its module is deliberately named to score HIGH on the ticket keywords, so
    a map that ingests it will rank vendored code as task-relevant. That is the failure, not a
    cosmetic one: it is what the model reads first.
    """
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    # Two matching symbols in ONE file — the property the old one-symbol-per-file path cannot show.
    (pkg / "foo.py").write_text(
        "def parse_widget(data):\n    return helper_fn(data)\n\n"
        "def helper_fn(x):\n    return x\n"
    )
    (pkg / "bar.py").write_text(
        "from pkg.foo import parse_widget\n\ndef other_thing():\n    return parse_widget({})\n"
    )

    venv = root / "test_env"
    (venv / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr\n")
    (venv / "lib" / "python3.12" / "site-packages" / "widget_junk.py").write_text(
        "def parse_widget_vendored(data):\n    return data\n"
    )
    return root


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


def test_the_graph_map_surfaces_two_symbols_and_ingests_no_vendored_code(tmp_path):
    """The map shows a file's SECOND symbol, and never shows a vendored one.

    RED (name-blacklist skip): `test_env/.../widget_junk.py` is walked like source and, because
    its symbol matches the ticket keywords, it is ranked into the packet the model reads.
    GREEN: virtualenvs are detected by MECHANISM (a `pyvenv.cfg` at their root, and the
    `site-packages` directory itself), not by guessing their name.
    """
    repo = _make_repo(tmp_path)
    packet = build_graph_map(_TICKET, repo, budget_chars=4000)

    assert packet, "the graph map must render a packet for a repo with matching symbols"

    # 1. multi-symbol: pkg/foo.py contributes BOTH symbols, not just its top-scored one.
    assert "parse_widget" in packet and "helper_fn" in packet, (
        "the graph map must surface a file's SECOND matching symbol (helper_fn) — the bare "
        f"one-symbol-per-file list drops it. Packet rendered:\n{packet}"
    )

    # 2. no vendored code: the venv contributes NOTHING, however well its symbols score.
    assert "widget_junk" not in packet and "parse_widget_vendored" not in packet, (
        "the graph map ingested a virtualenv inside the repo. Its skip list matches directory "
        "NAMES, so a venv called anything but `.venv`/`venv` is walked as source — this repo's "
        "own `test_env/` (2173 files) ranks pip's vendored code above the task's files in every "
        f"DickSimnel orientation. Packet rendered:\n{packet}"
    )


def test_the_map_still_ranks_the_repos_own_files(tmp_path):
    """Guard the fix against over-skipping: real source must survive the venv exclusion.

    A skip predicate that is too eager is the same bug wearing the other hat — an empty map
    fails open to the keyword path and the model silently loses its orientation entirely.
    """
    repo = _make_repo(tmp_path)
    packet = build_graph_map(_TICKET, repo, budget_chars=4000)
    assert "pkg/foo.py" in packet, f"the repo's own source vanished from the map:\n{packet}"
    assert "pkg/bar.py" in packet, f"a referencing file vanished from the map:\n{packet}"
