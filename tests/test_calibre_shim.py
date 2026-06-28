"""Tests for CalibreDevice and CalibreShim.

Completion criteria (T-calibre-shim):
  - search_books returns books list; no SQLite import
  - get_book_metadata returns structured result
  - list_books with author/tag filter passes correct search arg
  - shim returns structured error when calibredb not found
  - device registers in skeleton (smoke: instantiation works)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.calibre.device import CalibreDevice, _calibredb_available
from unseen_university.devices.calibre.shim import CalibreShim


class TestCalibreDevice:
    def _device(self, **kwargs):
        return CalibreDevice(
            library_path="/tmp/fake_library",
            calibredb_path="calibredb",
            **kwargs,
        )

    def _mock_run(self, stdout: str = "", returncode: int = 0):
        return MagicMock(returncode=returncode, stdout=stdout, stderr="")

    def test_no_sqlite_import(self):
        """CalibreDevice must not import sqlite3 — calibredb handles all DB access."""
        import unseen_university.devices.calibre.device as mod
        import sys
        # If sqlite3 were imported at module level it would be in sys.modules
        # The device should NOT import sqlite3 or sqlite3 from within its own code
        import ast, inspect
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, 'names', []):
                    imports.append(alias.name)
                if hasattr(node, 'module') and node.module:
                    imports.append(node.module)
        assert not any('sqlite' in i for i in imports), \
            "CalibreDevice must not import sqlite3 or any sqlite module"

    def test_search_books_calls_calibredb(self):
        device = self._device()
        with patch("subprocess.run", return_value=self._mock_run("1, 2, 3")):
            with patch.object(device, "get_book_metadata", return_value={"title": "Mock", "book_id": "1"}):
                result = device.search_books("Pratchett", limit=3)
        assert "books" in result
        assert len(result["books"]) == 3

    def test_search_books_no_results(self):
        device = self._device()
        with patch("subprocess.run", return_value=self._mock_run("")):
            result = device.search_books("xyzxyz_not_found")
        assert result["books"] == []

    def test_search_books_calibredb_missing_returns_error(self):
        device = self._device()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = device.search_books("Pratchett")
        assert "error" in result

    def test_list_books_author_filter(self):
        device = self._device()
        captured_cmds = []

        def _run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return self._mock_run("id title authors\n1 Book Author\n")

        with patch("subprocess.run", side_effect=_run):
            device.list_books(author="Pratchett")

        assert any("authors:Pratchett" in str(cmd) for cmd in captured_cmds)

    def test_calibredb_unavailable_health_degraded(self):
        device = self._device()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            h = device.health()
        assert h["status"] == "degraded"
        assert not h["calibredb_available"]


class TestCalibreShim:
    def test_start_returns_true(self):
        shim = CalibreShim()
        assert shim.start() is True

    def test_stop_returns_true(self):
        shim = CalibreShim()
        assert shim.stop() is True

    def test_self_test_failed_when_calibredb_absent(self):
        shim = CalibreShim()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = shim.self_test()
        assert result["passed"] is False
        assert "not found" in result["details"].lower()

    def test_self_test_passes_when_calibredb_present(self):
        shim = CalibreShim()
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = shim.self_test()
        assert result["passed"] is True

    def test_device_id_is_calibre_0(self):
        shim = CalibreShim()
        assert shim.device_id == "calibre.0"
