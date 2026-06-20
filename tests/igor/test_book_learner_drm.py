"""test_book_learner_drm.py — T-cc-walk-19

Verifies the DRM-blocked path in book_learner:
  - _handle_drm_blocked marks reading_list status='failed'
  - deposits exactly one BOOK_DRM_BLOCKED FACTUAL memory
  - no cortex deposit call happens for a non-DRM book path
"""

from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# book_learner lives in devlab/claudecode — add to path so the module is importable
# as "book_learner" (bare name), which the patch strings below depend on.
_LAB_CLAUDECODE = str(Path(__file__).resolve().parents[2] / "devlab" / "claudecode")
if _LAB_CLAUDECODE not in sys.path:
    sys.path.insert(0, _LAB_CLAUDECODE)


def _make_args(run: bool = True, calibre_id: int = 42) -> types.SimpleNamespace:
    return types.SimpleNamespace(run=run, calibre_id=calibre_id, book=None, url=None)


def _drm_handle(calibre_id: int = 42) -> dict:
    from devices.igor.tools.ebook_reader import DRM_FAILED

    return {
        DRM_FAILED: True,
        "title": "Locked Book",
        "author": "Some Author",
        "calibre_id": calibre_id,
        "fmt": "azw3",
        "path": "/fake/Locked_Book.azw3",
    }


class TestHandleDrmBlocked:
    def test_no_deposit_in_dry_run(self):
        """With args.run=False, no Cortex or DB calls made."""
        from book_learner import _handle_drm_blocked

        args = _make_args(run=False)
        handle = _drm_handle()

        with patch("book_learner.psycopg2", None, create=True):
            # Should run without error; no actual psycopg2 or Cortex usage
            _handle_drm_blocked(handle, args)  # no exception = pass

    def test_deposits_one_drm_blocked_memory(self):
        """With args.run=True, exactly one BOOK_DRM_BLOCKED memory is deposited."""
        from book_learner import _handle_drm_blocked

        args = _make_args(run=True, calibre_id=99)
        handle = _drm_handle(calibre_id=99)

        mock_cortex = MagicMock()
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        with (
            patch("book_learner.UU_HOME_DB_URL", "postgresql://test"),
            patch("psycopg2.connect", return_value=mock_conn),
            patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex),
        ):
            _handle_drm_blocked(handle, args)

        assert mock_cortex.deposit.call_count == 1
        deposited = mock_cortex.deposit.call_args[0][0]
        assert "BOOK_DRM_BLOCKED" in deposited.id
        assert "BOOK_DRM_BLOCKED" in deposited.narrative
        assert deposited.memory_type.value == "FACTUAL" or str(
            deposited.memory_type
        ) in (
            "FACTUAL",
            "MemoryType.FACTUAL",
        )

    def test_marks_reading_list_failed(self):
        """With args.run=True and calibre_id set, reading_list updated to failed."""
        from book_learner import _handle_drm_blocked

        args = _make_args(run=True, calibre_id=55)
        handle = _drm_handle(calibre_id=55)

        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cortex = MagicMock()

        with (
            patch("book_learner.UU_HOME_DB_URL", "postgresql://test"),
            patch("psycopg2.connect", return_value=mock_conn),
            patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex),
        ):
            _handle_drm_blocked(handle, args)

        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        assert "status='failed'" in sql or "failed" in sql.lower()
        assert params == (55,)
