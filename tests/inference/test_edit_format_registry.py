"""
Unit checks for the edit-format machinery (T-aider-port-editformat-conformance):
the whole-file dialect (parse + apply), offline conformance computation from synthetic corpus
records, and warm-lookup format selection (empty registry → block).
"""

from __future__ import annotations

from unseen_university.devices.inference.block_apply import (
    apply_wholefile_to_dir,
    parse_wholefile,
)
from unseen_university.devices.inference import edit_format


def test_wholefile_parse_and_apply_overwrites(tmp_path):
    (tmp_path / "a.py").write_text("old = 1\n")
    completion = (
        "a.py\n```\nnew = 2\nnew2 = 3\n```\n\n"
        "sub/b.py\n```\nb = 1\n```\n"
    )
    parsed = parse_wholefile(completion)
    assert set(parsed) == {"a.py", "sub/b.py"}

    result = apply_wholefile_to_dir(completion, tmp_path)
    assert set(result.applied) == {"a.py", "sub/b.py"} and not result.parse_error
    assert (tmp_path / "a.py").read_text() == "new = 2\nnew2 = 3\n"
    assert (tmp_path / "sub" / "b.py").read_text() == "b = 1\n"


def test_wholefile_no_blocks_is_parse_error(tmp_path):
    result = apply_wholefile_to_dir("just some prose, no file blocks here", tmp_path)
    assert not result.applied and result.parse_error


def test_compute_conformance_from_synthetic_records():
    """Offline replay: per-(model, format) success rate = fraction of runs that applied ≥1 edit."""
    records = [
        {"model": "weak-9b", "format": "block", "applied": 0},
        {"model": "weak-9b", "format": "block", "applied": 0},
        {"model": "weak-9b", "format": "wholefile", "applied": 1},
        {"model": "weak-9b", "format": "wholefile", "applied": 2},
        {"model": "strong-70b", "format": "block", "applied": 3},
        {"model": "no-format-stamp"},  # predates stamping → ignored
    ]
    conf = edit_format.compute_conformance(records)
    assert conf[("weak-9b", "block")] == 0.0
    assert conf[("weak-9b", "wholefile")] == 1.0
    assert conf[("strong-70b", "block")] == 1.0
    assert ("no-format-stamp", None) not in conf


def test_select_edit_format_warm_lookup():
    # Empty registry (today's state) → block; the runtime ladder does the real work.
    assert edit_format.select_edit_format({}) == edit_format.BLOCK
    # Weak model conforms far better to whole-file → warm lookup moves it off block.
    weak = edit_format.conformance_for_model(
        {("weak-9b", "block"): 0.0, ("weak-9b", "wholefile"): 1.0}, "weak-9b")
    assert edit_format.select_edit_format(weak) == edit_format.WHOLEFILE
    # A tie stays on the conservative default (block).
    assert edit_format.select_edit_format({"block": 0.9, "wholefile": 0.9}) == edit_format.BLOCK
