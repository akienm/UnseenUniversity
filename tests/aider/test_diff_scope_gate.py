"""Red->green proof for the aider diff-scope safety gate.

The gate is the "did aider go rogue" guard: it must BLOCK when aider edits a test
file (making tests pass by editing them) or escapes the repo, and must PASS a
clean in-scope edit. The overlap-with-affected-files check is advisory (warn only)
because the affected-files list is unreliable prose (advisor 2026-07-06).

A hollow gate (always-pass) fails `test_edited_test_file_is_blocked`.
"""

from unseen_university.devices.aider.runner import evaluate_diff_scope


def test_clean_in_scope_edit_passes():
    v = evaluate_diff_scope(["shop/models.py", "shop/service.py"],
                            affected_files=["shop/models.py", "shop/service.py"])
    assert v["blocked"] is False
    assert v["reasons"] == []
    assert v["warnings"] == []


def test_edited_test_file_is_blocked():
    # The load-bearing red-form: aider must not edit tests to make them pass.
    v = evaluate_diff_scope(["shop/models.py", "tests/test_checkout.py"])
    assert v["blocked"] is True
    assert any("test file" in r for r in v["reasons"])


def test_test_named_file_at_root_is_blocked():
    v = evaluate_diff_scope(["test_ttl_cache.py"])
    assert v["blocked"] is True


def test_conftest_edit_is_blocked():
    v = evaluate_diff_scope(["conftest.py"])
    assert v["blocked"] is True


def test_protected_github_path_is_blocked():
    v = evaluate_diff_scope([".github/workflows/ci.yml"])
    assert v["blocked"] is True
    assert any("protected" in r for r in v["reasons"])


def test_path_escape_is_blocked():
    v = evaluate_diff_scope(["/etc/passwd"])
    assert v["blocked"] is True
    assert any("escapes" in r for r in v["reasons"])
    v2 = evaluate_diff_scope(["../outside.py"])
    assert v2["blocked"] is True


def test_out_of_scope_edit_warns_but_does_not_block():
    # Advisory: touching a file not in the declared list warns, never blocks.
    v = evaluate_diff_scope(["shop/extra.py"], affected_files=["shop/models.py"])
    assert v["blocked"] is False
    assert any("outside declared affected-files" in w for w in v["warnings"])


def test_no_affected_list_means_no_scope_warnings():
    v = evaluate_diff_scope(["shop/anything.py"], affected_files=None)
    assert v["blocked"] is False
    assert v["warnings"] == []


def test_windows_backslash_paths_normalized():
    v = evaluate_diff_scope(["tests\\test_x.py"])
    assert v["blocked"] is True
