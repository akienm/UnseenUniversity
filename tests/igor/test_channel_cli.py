"""CLI strict-flag tests for lab/claudecode/channel.py.

T-channel-post-strict-flags: unknown flags must exit non-zero instead of
silently passing through as positional content (or being dropped), which
previously lost messages when '--author cc' was typed instead of '--as cc'.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CHANNEL = Path(__file__).resolve().parents[1] / "lab" / "claudecode" / "channel.py"


def _run(args, runtime_root):
    """Run channel.py with a tmp runtime root so tests never touch real state."""
    env = {
        "PATH": "/usr/bin:/bin",
        "IGOR_RUNTIME_ROOT": str(runtime_root),
        "IGOR_HOME_DB_URL": "",
        "IGOR_DB_URL": "",
    }
    return subprocess.run(
        [sys.executable, str(CHANNEL), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def test_post_accepts_known_flag(tmp_path):
    r = _run(["post", "hello", "--as", "test"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Posted:" in r.stdout


def test_post_rejects_unknown_flag(tmp_path):
    r = _run(["post", "hello", "--author", "test"], tmp_path)
    assert r.returncode == 2
    assert "unknown flag: --author" in r.stderr


def test_post_rejects_typo_before_content(tmp_path):
    r = _run(["post", "--bogus", "hello"], tmp_path)
    assert r.returncode == 2
    assert "unknown flag: --bogus" in r.stderr


def test_post_flag_without_value_errors(tmp_path):
    r = _run(["post", "hello", "--as"], tmp_path)
    assert r.returncode == 2
    assert "--as requires a value" in r.stderr


def test_read_accepts_positional_count(tmp_path):
    _run(["post", "msg", "--as", "t"], tmp_path)
    r = _run(["read", "5"], tmp_path)
    assert r.returncode == 0


def test_read_rejects_unknown_flag(tmp_path):
    r = _run(["read", "--count", "5"], tmp_path)
    assert r.returncode == 2
    assert "unknown flag: --count" in r.stderr


def test_listen_rejects_unknown_flag(tmp_path):
    r = _run(["listen", "--follow"], tmp_path)
    assert r.returncode == 2
    assert "unknown flag: --follow" in r.stderr


def test_sessions_rejects_unknown_flag(tmp_path):
    r = _run(["sessions", "--window", "30"], tmp_path)
    assert r.returncode == 2
    assert "unknown flag: --window" in r.stderr


def test_sessions_plain_works(tmp_path):
    r = _run(["sessions"], tmp_path)
    assert r.returncode == 0
