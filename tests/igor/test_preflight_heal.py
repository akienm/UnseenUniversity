"""
test_preflight_heal.py — unit tests for the pre-flight failure classifier
and SocketRecvNoMockRecognizer + heal_and_commit integration.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools.preflight_heal import (
    EditDict,
    HealResult,
    RECOGNIZERS,
    Recognizer,
    SocketRecvNoMockRecognizer,
    _ensure_imports,
    apply_heal,
    classify,
    heal_and_commit,
)

SAMPLE_SOCKET_RECV_FAILURE = """\
============================= test session starts ==============================
collected 123 items

tests/test_thing.py F                                                    [  1%]

=================================== FAILURES ===================================
__________________________ test_fetches_remote_data ___________________________

    def test_fetches_remote_data():
>       resp = urlopen("http://example.com/api", timeout=30)

tests/test_thing.py:42:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

>           return self._sock.recv(max_bytes)
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^
E           socket.timeout: ReadTimeout

=========================== short test summary info ============================
FAILED tests/test_thing.py::test_fetches_remote_data - socket.timeout: ReadTimeout
"""


# ── SocketRecvNoMockRecognizer.matches ────────────────────────────────────────


class TestSocketRecvMatches:
    def test_matches_on_socket_timeout(self):
        rec = SocketRecvNoMockRecognizer()
        assert rec.matches(SAMPLE_SOCKET_RECV_FAILURE) is True

    def test_matches_on_bare_sock_recv(self):
        rec = SocketRecvNoMockRecognizer()
        assert rec.matches(">           return self._sock.recv(max_bytes)") is True

    def test_matches_on_readtimeout(self):
        rec = SocketRecvNoMockRecognizer()
        assert rec.matches("urllib3.exceptions.ReadTimeout: timeout") is True

    def test_no_match_on_unrelated_failure(self):
        rec = SocketRecvNoMockRecognizer()
        assert rec.matches("AssertionError: expected 1 but got 2") is False


# ── SocketRecvNoMockRecognizer.remedy ─────────────────────────────────────────


@pytest.fixture
def tmp_repo(tmp_path):
    """A tmp repo with a tests/ folder and a sample failing test file."""
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_thing.py"
    test_file.write_text(
        '"""Sample test module."""\n'
        "\n"
        "def test_fetches_remote_data():\n"
        "    assert True\n"
    )
    return tmp_path


class TestSocketRecvRemedy:
    def test_remedy_produces_skipif_edit(self, tmp_repo):
        rec = SocketRecvNoMockRecognizer()
        edits = rec.remedy(SAMPLE_SOCKET_RECV_FAILURE, tmp_repo)
        assert len(edits) == 1
        edit = edits[0]
        assert edit.file == "tests/test_thing.py"
        assert edit.old_string == "def test_fetches_remote_data("
        assert "@pytest.mark.skipif" in edit.new_string
        assert "IGOR_LIVE_TESTS" in edit.new_string
        assert edit.new_string.endswith(edit.old_string)

    def test_remedy_is_idempotent_if_already_skipped(self, tmp_repo):
        test_file = tmp_repo / "tests" / "test_thing.py"
        test_file.write_text(
            '"""Sample test module."""\n'
            "\n"
            "import os\n"
            "import pytest\n"
            "\n"
            '@pytest.mark.skipif(not os.getenv("IGOR_LIVE_TESTS"), reason="live")\n'
            "def test_fetches_remote_data():\n"
            "    assert True\n"
        )
        rec = SocketRecvNoMockRecognizer()
        edits = rec.remedy(SAMPLE_SOCKET_RECV_FAILURE, tmp_repo)
        assert edits == []

    def test_remedy_empty_when_no_failed_line(self, tmp_repo):
        rec = SocketRecvNoMockRecognizer()
        edits = rec.remedy("socket.timeout without a FAILED line", tmp_repo)
        assert edits == []

    def test_remedy_empty_when_test_file_missing(self, tmp_repo):
        rec = SocketRecvNoMockRecognizer()
        bogus = SAMPLE_SOCKET_RECV_FAILURE.replace("test_thing", "nonexistent_test")
        edits = rec.remedy(bogus, tmp_repo)
        assert edits == []


# ── classify() ────────────────────────────────────────────────────────────────


class TestClassify:
    def test_matching_failure_returns_healed_result(self, tmp_repo):
        result = classify(SAMPLE_SOCKET_RECV_FAILURE, tmp_repo)
        assert result.healed is True
        assert result.recognizer == "socket-recv-no-mock"
        assert len(result.edits) == 1

    def test_non_matching_failure_returns_unfixable(self, tmp_repo):
        result = classify("AssertionError: expected 1 got 2", tmp_repo)
        assert result.healed is False
        assert result.recognizer is None
        assert len(result.unfixable) == 1


# ── apply_heal() + _ensure_imports ────────────────────────────────────────────


class TestApplyHeal:
    def test_apply_inserts_decorator_and_imports(self, tmp_repo):
        result = classify(SAMPLE_SOCKET_RECV_FAILURE, tmp_repo)
        assert apply_heal(result, tmp_repo) is True
        content = (tmp_repo / "tests" / "test_thing.py").read_text()
        assert "@pytest.mark.skipif" in content
        assert "import pytest" in content
        assert "import os" in content

    def test_apply_fails_if_old_string_missing(self, tmp_repo):
        result = HealResult(
            healed=True,
            recognizer="bogus",
            edits=[
                EditDict(
                    file="tests/test_thing.py",
                    old_string="def does_not_exist(",
                    new_string="def replaced(",
                )
            ],
        )
        assert apply_heal(result, tmp_repo) is False


class TestEnsureImports:
    def test_adds_pytest_import_when_missing(self):
        content = '"""doc."""\n\ndef foo(): pass\n'
        out = _ensure_imports(content, "@pytest.mark.skipif(...)")
        assert "import pytest" in out

    def test_adds_os_import_when_missing(self):
        content = '"""doc."""\n\ndef foo(): pass\n'
        out = _ensure_imports(content, "os.getenv('FOO')")
        assert "import os" in out

    def test_no_duplicate_imports(self):
        content = "import pytest\nimport os\n\ndef foo(): pass\n"
        out = _ensure_imports(content, "@pytest.mark.skipif(os.getenv('X'), ...)")
        assert out.count("import pytest") == 1
        assert out.count("import os") == 1


# ── heal_and_commit() full flow ───────────────────────────────────────────────


@pytest.fixture
def tmp_git_repo(tmp_path):
    """A tmp repo with git initialized and a sample failing test committed."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_thing.py"
    test_file.write_text(
        '"""Sample test module."""\n'
        "\n"
        "def test_fetches_remote_data():\n"
        "    assert True\n"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


class TestHealAndCommit:
    def test_full_flow_heals_and_commits(self, tmp_git_repo):
        result = heal_and_commit(SAMPLE_SOCKET_RECV_FAILURE, tmp_git_repo)
        assert result.healed is True
        assert result.recognizer == "socket-recv-no-mock"
        assert result.commit_sha is not None
        # The test file contains the new decorator
        content = (tmp_git_repo / "tests" / "test_thing.py").read_text()
        assert "@pytest.mark.skipif" in content
        # Latest commit message mentions the recognizer
        log_out = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "socket-recv-no-mock" in log_out
        assert "preflight auto-heal" in log_out

    def test_non_matching_output_returns_unfixable(self, tmp_git_repo):
        result = heal_and_commit("boring AssertionError", tmp_git_repo)
        assert result.healed is False
        assert result.commit_sha is None
