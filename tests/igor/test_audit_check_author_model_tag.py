"""
test_audit_check_author_model_tag.py — T-author-model-header-on-new-files

# author-model: opus

Tests for the author-model header tag check.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.claudecode.audit_check_author_model_tag import (  # noqa: E402
    check_files,
    has_author_model_tag,
    is_enforced_path,
    is_recognized_token,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEVICES_IGOR = REPO_ROOT / "devices" / "igor"


class TestHasAuthorModelTag:
    def test_comment_form_recognized(self):
        content = '"""docstring."""\n# author-model: opus\nimport x\n'
        ok, token = has_author_model_tag(content)
        assert ok
        assert token == "opus"

    def test_in_docstring_recognized(self):
        content = '"""docstring.\n\nauthor-model: sonnet\n"""\nimport x\n'
        ok, token = has_author_model_tag(content)
        assert ok
        assert token == "sonnet"

    def test_case_insensitive(self):
        content = "# AUTHOR-MODEL: HAIKU\n"
        ok, token = has_author_model_tag(content)
        assert ok
        assert token == "haiku"

    def test_missing_returns_false(self):
        content = '"""no tag here."""\nimport x\n'
        ok, token = has_author_model_tag(content)
        assert not ok
        assert token == ""

    def test_only_scans_top_30_lines(self):
        # Tag buried below line 30 should NOT count
        content = "\n".join(["# nothing"] * 35 + ["# author-model: opus"])
        ok, _ = has_author_model_tag(content)
        assert not ok

    def test_handles_extra_whitespace(self):
        content = "#   author-model :   opus   \n"
        ok, token = has_author_model_tag(content)
        assert ok
        assert token == "opus"


class TestIsRecognizedToken:
    def test_opus(self):
        assert is_recognized_token("opus")

    def test_sonnet(self):
        assert is_recognized_token("sonnet")

    def test_haiku(self):
        assert is_recognized_token("haiku")

    def test_human(self):
        assert is_recognized_token("human")

    def test_igor(self):
        assert is_recognized_token("igor")

    def test_unknown_rejected(self):
        assert not is_recognized_token("gpt4")

    def test_empty_rejected(self):
        assert not is_recognized_token("")

    def test_substring_match(self):
        # "opus-1m" contains "opus" → ok
        assert is_recognized_token("opus-1m")


class TestIsEnforcedPath:
    def test_devices_igor_py_enforced(self, tmp_path):
        target = _DEVICES_IGOR / "_synthetic_for_test.py"
        assert is_enforced_path(target) is True

    def test_init_py_exempt(self):
        target = _DEVICES_IGOR / "__init__.py"
        assert is_enforced_path(target) is False

    def test_tests_dir_exempt(self):
        target = REPO_ROOT / "tests" / "_synthetic.py"
        assert is_enforced_path(target) is False

    def test_non_py_exempt(self):
        target = _DEVICES_IGOR / "foo.md"
        assert is_enforced_path(target) is False

    def test_outside_enforced_dirs_exempt(self):
        target = REPO_ROOT / "papers" / "thoughts" / "thing.py"
        assert is_enforced_path(target) is False


class TestCheckFiles:
    def test_compliant_file_passes(self, tmp_path):
        import lab.claudecode.audit_check_author_model_tag as _mod

        target = _DEVICES_IGOR / "_synthetic_compliant.py"
        target.write_text('"""ok."""\n# author-model: opus\nx = 1\n')
        try:
            with patch.object(_mod, "REPO_ROOT", REPO_ROOT):
                assert check_files([target]) == []
        finally:
            target.unlink()

    def test_missing_tag_flagged(self):
        import lab.claudecode.audit_check_author_model_tag as _mod

        target = _DEVICES_IGOR / "_synthetic_no_tag.py"
        target.write_text('"""ok."""\nx = 1\n')
        try:
            with patch.object(_mod, "REPO_ROOT", REPO_ROOT):
                violations = check_files([target])
            assert len(violations) == 1
            assert "missing 'author-model:'" in violations[0]
        finally:
            target.unlink()

    def test_unrecognized_token_flagged(self):
        import lab.claudecode.audit_check_author_model_tag as _mod

        target = _DEVICES_IGOR / "_synthetic_bad_token.py"
        target.write_text("# author-model: gpt4\nx = 1\n")
        try:
            with patch.object(_mod, "REPO_ROOT", REPO_ROOT):
                violations = check_files([target])
            assert len(violations) == 1
            assert "not recognized" in violations[0]
        finally:
            target.unlink()

    def test_exempt_file_skipped(self):
        target = REPO_ROOT / "tests" / "_synthetic_test_exempt.py"
        target.write_text('"""no tag, but exempt."""\nx = 1\n')
        try:
            assert check_files([target]) == []
        finally:
            target.unlink()

    def test_nonexistent_file_silently_skipped(self):
        target = REPO_ROOT / "lab" / "claudecode" / "_synthetic_does_not_exist.py"
        assert check_files([target]) == []


class TestGetNewFilesInRange:
    def test_returns_added_files_only(self):
        from lab.claudecode.audit_check_author_model_tag import (
            get_new_files_in_range,
        )

        # Mock the git diff call
        class _R:
            returncode = 0
            stdout = "lab/claudecode/new_file.py\ndevices/igor/another.py\n"

        with patch(
            "lab.claudecode.audit_check_author_model_tag.subprocess.run",
            return_value=_R(),
        ):
            paths = get_new_files_in_range("HEAD~1..HEAD")
        assert len(paths) == 2
        assert all(p.is_absolute() for p in paths)

    def test_empty_when_git_fails(self):
        from lab.claudecode.audit_check_author_model_tag import (
            get_new_files_in_range,
        )

        class _R:
            returncode = 1
            stdout = ""

        with patch(
            "lab.claudecode.audit_check_author_model_tag.subprocess.run",
            return_value=_R(),
        ):
            assert get_new_files_in_range("bogus") == []
