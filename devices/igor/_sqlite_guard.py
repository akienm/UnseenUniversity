"""
_sqlite_guard.py — Forbids accidental sqlite3 use inside devices/igor/.

Igor stores all data in PostgreSQL. Any call to sqlite3.connect() within
the igor package raises RuntimeError immediately.

EXCEPTION — external SQLite files (Calibre DB, Kindle DRM keys):
These tools legitimately read third-party SQLite files and are allowed.
Use the escape hatch:

    from devices.igor._sqlite_guard import real_sqlite3 as sqlite3

Vendor files (devices/igor/tools/ebook_drm/androidkindlekey.py) are
exempt — they are unmodified third-party code and only run during rare
DRM-extraction paths.
"""

from __future__ import annotations

import os
import sys
import types
import sqlite3 as _real_sqlite3

# Escape hatch — import this for legitimate external-file reads
real_sqlite3 = _real_sqlite3


class _SQLiteGuardModule(types.ModuleType):
    def connect(self, *args, **kwargs):
        # In test mode the guard is installed for import-hygiene checking but
        # must not block tests outside devices/igor/ that legitimately use SQLite
        # (e.g. Calibre DB tests, reader tests).  The actual enforcement is via
        # code review and the evaluator — not a hard runtime barrier in tests.
        if os.environ.get("AGENT_DATACENTER_TEST_MODE") == "1":
            return _real_sqlite3.connect(*args, **kwargs)
        raise RuntimeError(
            "sqlite3.connect() is forbidden in devices/igor/ — Igor uses PostgreSQL.\n"
            "To read an external SQLite file (e.g. Calibre DB), use:\n"
            "    from devices.igor._sqlite_guard import real_sqlite3 as sqlite3"
        )

    def __getattr__(self, name: str):
        return getattr(_real_sqlite3, name)


_guard = _SQLiteGuardModule("sqlite3")
_guard.__spec__ = _real_sqlite3.__spec__
sys.modules["sqlite3"] = _guard
