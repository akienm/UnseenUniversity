"""
Pure ladder + parser proof for block_apply (T-aider-port-editor-block-contract).

Three failure modes the DS editor died on, exercised on the deterministic apply module with a
FIXED completion string (no LLM):
  (a) ONE completion carrying N SEARCH/REPLACE blocks applies all N.
  (b) a whitespace-mangled SEARCH block STILL applies via the forgiving ladder (fixes F-C).
  (c) a genuinely non-matching SEARCH block fails CLEANLY — recorded in `failed`, file
      untouched, never a silent skip.
"""

from __future__ import annotations

from unseen_university.agentic.block_apply import apply_blocks_to_dir


def test_multiple_blocks_in_one_completion_all_apply(tmp_path):
    """(a) A single completion with two SEARCH/REPLACE blocks applies BOTH edits."""
    f = tmp_path / "mod.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")
    completion = (
        "mod.py\n"
        "<<<<<<< SEARCH\n"
        "a = 1\n"
        "=======\n"
        "a = 10\n"
        ">>>>>>> REPLACE\n"
        "\n"
        "mod.py\n"
        "<<<<<<< SEARCH\n"
        "c = 3\n"
        "=======\n"
        "c = 30\n"
        ">>>>>>> REPLACE\n"
    )
    res = apply_blocks_to_dir(completion, tmp_path)
    assert res.applied == ["mod.py", "mod.py"], f"both blocks should apply: {res}"
    assert not res.failed and not res.parse_error
    assert f.read_text() == "a = 10\nb = 2\nc = 30\n"


def test_whitespace_mangled_block_still_applies(tmp_path):
    """(b) A SEARCH block that drops the file's leading indent still matches via the ladder."""
    f = tmp_path / "svc.py"
    f.write_text("def f():\n    x = 1\n    return x\n")
    # SEARCH/REPLACE are OUTDENTED — the model dropped the 4-space indent uniformly. The exact
    # match fails; replace_part_with_missing_leading_whitespace re-adds the indent and matches.
    completion = (
        "svc.py\n"
        "<<<<<<< SEARCH\n"
        "x = 1\n"
        "return x\n"
        "=======\n"
        "x = 2\n"
        "return x\n"
        ">>>>>>> REPLACE\n"
    )
    res = apply_blocks_to_dir(completion, tmp_path)
    assert res.applied == ["svc.py"], f"whitespace-forgiving ladder should apply it: {res}"
    # The re-indented replacement lands with the file's original indentation preserved.
    assert f.read_text() == "def f():\n    x = 2\n    return x\n"


def test_nonmatching_block_fails_cleanly(tmp_path):
    """(c) A SEARCH block that matches nothing is recorded as failed; the file is untouched."""
    f = tmp_path / "keep.py"
    original = "value = 'unchanged'\n"
    f.write_text(original)
    completion = (
        "keep.py\n"
        "<<<<<<< SEARCH\n"
        "this text does not exist anywhere in the file\n"
        "=======\n"
        "replacement that must NOT be written\n"
        ">>>>>>> REPLACE\n"
    )
    res = apply_blocks_to_dir(completion, tmp_path)
    assert not res.applied, "a non-matching block must not report an apply"
    assert len(res.failed) == 1 and res.failed[0][0] == "keep.py"
    assert f.read_text() == original, "the file must be left untouched on a clean failure"
