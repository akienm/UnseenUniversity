"""
CalibreDevice — rack device for Calibre ebook library access.

Exposes 4 MCP tools (search_books, get_book_metadata, list_books, get_book_content)
via calibredb subprocess. No SQLite import; calibredb handles all DB access.
Falls back gracefully when calibredb is not installed or the library is offline.

Config (config/calibre.cfg or constructor kwargs):
    library_path = /media/akien/onedrive/AkiensMedia/Ebooks/Calibre Library
    calibredb_path = calibredb
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION

log = logging.getLogger(__name__)

_LIBRARY_PATH_DEFAULT = os.environ.get(
    "CALIBRE_LIBRARY_PATH",
    "/media/akien/onedrive/AkiensMedia/Ebooks/Calibre Library",
)
_CALIBREDB_DEFAULT = os.environ.get("CALIBREDB_PATH", "calibredb")


def _calibredb_available(calibredb_path: str = _CALIBREDB_DEFAULT) -> bool:
    try:
        r = subprocess.run([calibredb_path, "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


class CalibreDevice(BaseDevice):
    """Rack device for Calibre ebook library queries via calibredb CLI."""

    def __init__(
        self,
        library_path: str = _LIBRARY_PATH_DEFAULT,
        calibredb_path: str = _CALIBREDB_DEFAULT,
    ) -> None:
        super().__init__(device_id="calibre.0")
        self._library_path = library_path
        self._calibredb_path = calibredb_path
        self._start_time = time.time()

    @property
    def device_id(self) -> str:
        return "calibre.0"

    def who_am_i(self) -> dict:
        return {
            "device_id": self.device_id,
            "device_type": "calibre",
            "description": "Calibre ebook library query interface",
        }

    def requirements(self) -> dict:
        return {"calibredb": self._calibredb_path, "library_path": self._library_path}

    def capabilities(self) -> dict:
        return {
            "tools": [
                "mcp__calibre__search_books",
                "mcp__calibre__get_book_metadata",
                "mcp__calibre__list_books",
                "mcp__calibre__get_book_content",
            ]
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        available = _calibredb_available(self._calibredb_path)
        return {
            "status": "healthy" if available else "degraded",
            "calibredb_available": available,
            "library_path": self._library_path,
        }

    def uptime(self) -> float:
        return time.time() - self._start_time

    # ── Tool methods ──────────────────────────────────────────────────────────

    def _run_calibredb(self, *args: str, timeout: int = 30) -> tuple[bool, str]:
        """Run calibredb with the configured library path. Returns (ok, output)."""
        cmd = [self._calibredb_path, "--with-library", self._library_path, *args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                log.warning("calibredb %s failed: %s", args[0], r.stderr[:200])
                return False, r.stderr.strip()
            return True, r.stdout.strip()
        except FileNotFoundError:
            return False, f"calibredb not found at {self._calibredb_path!r}"
        except subprocess.TimeoutExpired:
            return False, "calibredb timed out"
        except Exception as exc:
            return False, str(exc)

    def search_books(self, query: str, limit: int = 20) -> dict[str, Any]:
        """Search books by title/author/tag/keyword. Returns ranked list."""
        ok, output = self._run_calibredb(
            "search", "--limit", str(limit), query
        )
        if not ok:
            return {"error": output, "books": []}
        # calibredb search returns comma-separated IDs
        ids = [i.strip() for i in output.split(",") if i.strip()]
        if not ids:
            return {"books": []}
        # Fetch metadata for results
        books = []
        for book_id in ids[:limit]:
            meta = self.get_book_metadata(book_id)
            if "error" not in meta:
                books.append(meta)
        return {"books": books}

    def get_book_metadata(self, book_id: str) -> dict[str, Any]:
        """Return full metadata for a single book by ID."""
        ok, output = self._run_calibredb("catalog", "--fields", "all", f"id:{book_id}")
        if not ok:
            # Fallback: list with id filter
            ok2, out2 = self._run_calibredb(
                "list", "--fields", "id,title,authors,tags,formats,pubdate,publisher",
                "--search", f"id:{book_id}", "--limit", "1"
            )
            if not ok2:
                return {"error": out2}
            return self._parse_list_output(out2)
        return {"raw": output[:2000], "book_id": book_id}

    def list_books(
        self,
        author: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Browse library by author or tag."""
        search_arg = ""
        if author:
            search_arg = f"authors:{author}"
        elif tag:
            search_arg = f"tags:{tag}"

        args = [
            "list",
            "--fields", "id,title,authors,tags,formats",
            "--limit", str(limit),
        ]
        if search_arg:
            args += ["--search", search_arg]

        ok, output = self._run_calibredb(*args)
        if not ok:
            return {"error": output, "books": []}
        return {"books": self._parse_list_output(output)}

    def get_book_content(self, book_id: str, format: str = "epub") -> dict[str, Any]:
        """Return file path for the requested format if available."""
        ok, output = self._run_calibredb(
            "list", "--fields", "id,formats", "--search", f"id:{book_id}", "--limit", "1"
        )
        if not ok:
            return {"error": output}
        # Find the requested format path in the output
        fmt_upper = format.upper()
        for line in output.splitlines():
            if fmt_upper in line:
                # Extract path from formats field
                parts = line.split()
                for p in parts:
                    if p.lower().endswith(f".{format.lower()}"):
                        return {"path": p, "format": format, "book_id": book_id}
        return {"error": f"format {format!r} not available for book {book_id}"}

    @staticmethod
    def _parse_list_output(output: str) -> list[dict]:
        """Parse tabular calibredb list output into a list of dicts."""
        lines = output.splitlines()
        if len(lines) < 2:
            return []
        books = []
        for line in lines[1:]:
            if line.strip():
                books.append({"raw": line[:300]})
        return books

    # ── BaseDevice abstract method implementations ────────────────────────────

    def comms(self) -> dict:
        return {"address": "comms://calibre.0/inbox", "mode": "read_write",
                "supports_push": False, "supports_pull": True, "supports_nudge": False}

    def startup_errors(self) -> list:
        return []

    def logs(self) -> dict:
        import pathlib
        return {"paths": {"device": str(pathlib.Path.home() / ".unseen_university" / "logs" / "calibre" / "device.log")}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        import os as _os
        return {"host": _os.environ.get("HOSTNAME", "localhost"), "pid": _os.getpid(),
                "launch_command": "python -m unseen_university.devices.calibre"}

    def restart(self) -> None:
        pass

    def block(self, reason: str) -> None:
        log.info("CalibreDevice: blocked (%s)", reason)

    def halt(self) -> None:
        log.info("CalibreDevice: halted")

    def recovery(self) -> None:
        pass
