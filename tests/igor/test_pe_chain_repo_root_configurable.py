# Author-model: Claude Haiku 4.5
"""Regression tests for T-pe-chain-respect-worktree-path-for-observe-implement.

Cert walk W-1 (2026-04-30) discovered that pe_chain hardcoded `_REPO_ROOT`
to `~/TheIgors`, causing the worktree-based replay-old protocol to silently
read from main's checkout instead of the worktree at HEAD~1. This test
locks in the fix: pe_chain reads from `IGOR_PE_CHAIN_REPO_ROOT` env var
when set, falls back to the default otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from wild_igor.igor.tools import pe_chain


def test_get_repo_root_default_when_env_unset():
    """Default behavior: returns ~/TheIgors when env var not set."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("IGOR_PE_CHAIN_REPO_ROOT", None)
        result = pe_chain._get_repo_root()
        assert result == Path.home() / "TheIgors"


def test_get_repo_root_honors_env_override():
    """When IGOR_PE_CHAIN_REPO_ROOT is set, _get_repo_root returns that path."""
    fake_path = "/tmp/fake-cert-walk-worktree"
    with patch.dict(os.environ, {"IGOR_PE_CHAIN_REPO_ROOT": fake_path}):
        result = pe_chain._get_repo_root()
        assert result == Path(fake_path)


def test_get_repo_root_returns_path_not_string():
    """Result is always a Path object regardless of env var presence."""
    with patch.dict(os.environ, {"IGOR_PE_CHAIN_REPO_ROOT": "/tmp/x"}):
        assert isinstance(pe_chain._get_repo_root(), Path)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("IGOR_PE_CHAIN_REPO_ROOT", None)
        assert isinstance(pe_chain._get_repo_root(), Path)


def test_get_repo_root_reads_env_per_call():
    """_get_repo_root reads the env per-call so changes are picked up live.

    This matters for debugger.start(repo_root=...) which sets the env var
    before invoking pe_chain — if _get_repo_root cached at module load,
    the override would never take effect.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("IGOR_PE_CHAIN_REPO_ROOT", None)
        before = pe_chain._get_repo_root()
        os.environ["IGOR_PE_CHAIN_REPO_ROOT"] = "/tmp/changed-mid-process"
        after = pe_chain._get_repo_root()
        assert before != after
        assert after == Path("/tmp/changed-mid-process")


def test_debugger_start_sets_env_when_repo_root_passed():
    """pe_chain_debugger.start(repo_root=...) sets IGOR_PE_CHAIN_REPO_ROOT.

    Doesn't actually run pe_chain (would need a fake ticket); just asserts
    the env-var side effect happens before pe_chain is invoked, which is
    the contract callers depend on.
    """
    from wild_igor.igor.tools import pe_chain_debugger as dbg

    fake_repo = "/tmp/test-cert-walk-worktree"
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("IGOR_PE_CHAIN_REPO_ROOT", None)

        # Call with an invalid breakpoint so we bail before the real run,
        # but the repo_root setter runs first.
        dbg.start(
            ticket_id="T-fake-for-test",
            breakpoint="HYPOTHESIZE",  # valid breakpoint
            repo_root=fake_repo,
        )

        # Whether the run succeeded or not, the env var should now be set.
        assert os.environ.get("IGOR_PE_CHAIN_REPO_ROOT") == fake_repo


def test_debugger_start_does_not_set_env_when_repo_root_omitted():
    """When repo_root kwarg is omitted, env var is left alone (Igor autonomous default)."""
    from wild_igor.igor.tools import pe_chain_debugger as dbg

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("IGOR_PE_CHAIN_REPO_ROOT", None)

        # Call without repo_root
        dbg.start(
            ticket_id="T-fake-for-test",
            breakpoint="HYPOTHESIZE",
        )

        # Env var should still be unset
        assert "IGOR_PE_CHAIN_REPO_ROOT" not in os.environ
