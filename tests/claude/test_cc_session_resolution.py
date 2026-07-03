"""Tests for _resolve_session_name() — compact cadence hook session targeting."""

from __future__ import annotations

from unittest.mock import patch


def _resolve(env=None, file_content=None, scan_result=None):
    """Call _resolve_session_name() under controlled conditions."""
    import importlib
    import unseen_university.devices.claude.constants as m

    env_patch = patch.dict("os.environ", env or {}, clear=False)
    file_read = patch.object(
        m.cc_session_path(),
        "read_text",
        side_effect=FileNotFoundError if file_content is None else lambda **kw: file_content,
    )
    scan_patch = patch.object(m, "_find_existing_cc_session", return_value=scan_result)

    with env_patch, scan_patch:
        if file_content is None:
            with patch("builtins.open", side_effect=FileNotFoundError):
                return m._resolve_session_name()
        else:
            # Patch cc_session_path().read_text via the path object
            from pathlib import Path
            with patch.object(Path, "read_text", return_value=file_content):
                return m._resolve_session_name()


class TestResolveSessionName:
    def test_env_var_wins(self):
        """CC_TMUX_SESSION env var is highest priority."""
        import os
        from unittest.mock import patch
        import unseen_university.devices.claude.constants as m

        with patch.dict(os.environ, {"CC_TMUX_SESSION": "env-session"}, clear=False), \
             patch.object(m, "_find_existing_cc_session", return_value="scan-session"):
            assert m._resolve_session_name() == "env-session"

    def test_file_used_when_no_env_var(self, tmp_path, monkeypatch):
        """cc_session.txt is read when CC_TMUX_SESSION is absent."""
        import os
        import unseen_university.devices.claude.constants as m

        session_file = tmp_path / "cc_session.txt"
        session_file.write_text("file-session\n")

        monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
        monkeypatch.setattr(m, "cc_session_path", lambda: session_file)
        monkeypatch.setattr(m, "_find_existing_cc_session", lambda: "scan-session")

        assert m._resolve_session_name() == "file-session"

    def test_scan_used_when_no_env_or_file(self, tmp_path, monkeypatch):
        """tmux scan is used when neither env var nor file exists."""
        import os
        import unseen_university.devices.claude.constants as m

        missing_file = tmp_path / "nonexistent.txt"
        monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
        monkeypatch.setattr(m, "cc_session_path", lambda: missing_file)
        monkeypatch.setattr(m, "_find_existing_cc_session", lambda: "claude-main")

        assert m._resolve_session_name() == "claude-main"

    def test_slot_find_fallback_when_nothing_found(self, tmp_path, monkeypatch):
        """_detect_session_name() runs when all other lookups fail."""
        import os
        import unseen_university.devices.claude.constants as m

        missing_file = tmp_path / "nonexistent.txt"
        monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
        monkeypatch.setattr(m, "cc_session_path", lambda: missing_file)
        monkeypatch.setattr(m, "_find_existing_cc_session", lambda: None)
        monkeypatch.setattr(m, "_detect_session_name", lambda: "slot-detected")

        assert m._resolve_session_name() == "slot-detected"

    def test_empty_file_falls_through_to_scan(self, tmp_path, monkeypatch):
        """An empty cc_session.txt is skipped; scan is tried next."""
        import os
        import unseen_university.devices.claude.constants as m

        session_file = tmp_path / "cc_session.txt"
        session_file.write_text("   \n")

        monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
        monkeypatch.setattr(m, "cc_session_path", lambda: session_file)
        monkeypatch.setattr(m, "_find_existing_cc_session", lambda: "claude-main")

        assert m._resolve_session_name() == "claude-main"


class TestFindExistingCcSession:
    def test_finds_hostname_pattern(self, monkeypatch):
        """Returns first hostname_cc_N session found."""
        import socket
        import subprocess
        import unseen_university.devices.claude.constants as m
        from unittest.mock import MagicMock

        hostname = socket.gethostname().split(".")[0].lower()
        fake = MagicMock(stdout=f"other-session\n{hostname}_cc_1\n{hostname}_cc_2\n", returncode=0)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)

        result = m._find_existing_cc_session()
        assert result == f"{hostname}_cc_1"

    def test_falls_back_to_claude_main(self, monkeypatch):
        """Returns claude-main when no hostname_cc_N session found."""
        import subprocess
        import unseen_university.devices.claude.constants as m
        from unittest.mock import MagicMock

        fake = MagicMock(stdout="other-session\nclaude-main\n", returncode=0)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)

        assert m._find_existing_cc_session() == "claude-main"

    def test_returns_none_when_nothing_matches(self, monkeypatch):
        """Returns None when no CC session pattern found."""
        import subprocess
        import unseen_university.devices.claude.constants as m
        from unittest.mock import MagicMock

        fake = MagicMock(stdout="unrelated-session\n", returncode=0)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)

        assert m._find_existing_cc_session() is None

    def test_returns_none_on_exception(self, monkeypatch):
        """Returns None gracefully when tmux is unavailable."""
        import subprocess
        import unseen_university.devices.claude.constants as m

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError("no tmux")))

        assert m._find_existing_cc_session() is None
