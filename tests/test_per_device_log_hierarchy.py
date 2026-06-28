"""Proof for T-per-device-log-hierarchy (D-skills-two-products).

Akien (2026-06-25): every rack device's logs land in ONE canonical per-device home,
~/.unseen_university/logs/<device>/, split into exactly three feed-aligned streams —
info / warn / debug — so the comm feeds (T-uu-readfeed) and shim-owned web buttons
(T-device-web-feed-channel-buttons) have a single place to read from.

The mechanism is the DiagnosticBase JSON sink (the design center: every device
inherits it). These tests prove:
  - records route to <root>/<device>/<stream>/ by level (info/warn/debug; WARNING+
    collapse to warn, exact level kept on the record) — the RED state is the old
    <device>/log/json/ layout;
  - the base sink never creates a flat file directly in the logs root;
  - the canonical default resolves to UU_LOG_ROOT, else uu_home()/logs (call-time);
  - the one known flat-file offender (web_server) is nested under a device dir.
"""
from __future__ import annotations

import time as _time
from pathlib import Path

import pytest

# NOTE: only parent-resident symbols are imported at module scope. The net-new
# symbols (_default_log_root, _level_stream) are imported INSIDE the tests that
# use them — so this module still COLLECTS against the pre-implementation tree
# (where those symbols don't exist yet). That keeps the behavioral discriminator
# (TestPerLevelRouting::test_levels_route_to_streams) able to prove itself
# red→green via proof_emitter: a module-level import of a net-new symbol would
# ImportError on the red side, which the proof harness rejects as collateral
# (not an assertion about behavior). See proof_emitter.py "stub-first convention".
from unseen_university.diagnostic_base.base import DiagnosticBase, _logger_cache


def _emit(root: Path, device_id: str, level: str, msg: str) -> None:
    """Emit one record from a default-constructed device pinned to `root`."""
    class _Dev(DiagnosticBase):
        _log_root = root

    _logger_cache.pop((_Dev, str(root)), None)
    dev = _Dev(device_id=device_id)
    getattr(dev, level)(msg)
    _time.sleep(0.05)  # loguru enqueue=False → near-instant flush


class TestLevelStreamMapping:
    def test_three_feed_streams(self):
        from unseen_university.diagnostic_base.base import _level_stream

        assert _level_stream("DEBUG") == "debug"
        assert _level_stream("TRACE") == "debug"
        assert _level_stream("INFO") == "info"
        assert _level_stream("SUCCESS") == "info"
        assert _level_stream("WARNING") == "warn"
        # WARNING and above collapse to the single attention stream Akien named.
        assert _level_stream("ERROR") == "warn"
        assert _level_stream("CRITICAL") == "warn"

    def test_unknown_level_defaults_to_info(self):
        from unseen_university.diagnostic_base.base import _level_stream

        assert _level_stream("NOTAREALLEVEL") == "info"


class TestPerLevelRouting:
    def test_levels_route_to_streams(self, tmp_path):
        """info→info/, warn+error→warn/, debug→debug/ under <root>/<device>/."""
        root = tmp_path / "logs"
        _emit(root, "router", "info", "an info record")
        _emit(root, "router", "warning", "a warn record")
        _emit(root, "router", "error", "an error record")
        _emit(root, "router", "debug", "a debug record")

        dev_dir = root / "router"
        assert list((dev_dir / "info").glob("*_info.json")), "info record not in info/"
        assert list((dev_dir / "debug").glob("*_debug.json")), "debug record not in debug/"
        warn_names = [f.name for f in (dev_dir / "warn").glob("*.json")]
        assert any(n.endswith("_warning.json") for n in warn_names), \
            "warning record not in warn/"
        assert any(n.endswith("_error.json") for n in warn_names), \
            "error record not routed to warn/ (WARNING+ collapses to warn)"
        # The OLD layout must not exist — this is the red→green discriminator.
        assert not (dev_dir / "log" / "json").exists(), "legacy log/json/ layout still used"

    def test_no_flat_file_in_logs_root(self, tmp_path):
        """The base sink only ever writes under <root>/<device>/<stream>/ —
        nothing lands as a flat file directly in the logs root."""
        root = tmp_path / "logs"
        _emit(root, "tidy", "info", "hi")
        top_level = list(root.iterdir())
        assert top_level, "nothing written at all"
        assert all(p.is_dir() for p in top_level), \
            f"flat file(s) in logs root: {[p.name for p in top_level if p.is_file()]}"

    def test_exact_level_preserved_on_record(self, tmp_path):
        """Collapsing to warn loses no information: the precise level stays in the
        filename and the JSON payload."""
        import json

        root = tmp_path / "logs"
        _emit(root, "keep", "error", "boom")
        files = list((root / "keep" / "warn").glob("*_error.json"))
        assert files, "error file not found in warn/"
        payload = json.loads(files[0].read_text())
        assert payload["level"] == "ERROR"


class TestCanonicalDefault:
    def test_uu_log_root_override_wins(self, tmp_path, monkeypatch):
        from unseen_university.diagnostic_base.base import _default_log_root

        monkeypatch.setenv("UU_LOG_ROOT", str(tmp_path / "override"))
        assert _default_log_root() == tmp_path / "override"

    def test_falls_back_to_uu_home_logs(self, tmp_path, monkeypatch):
        from unseen_university.diagnostic_base.base import _default_log_root

        monkeypatch.delenv("UU_LOG_ROOT", raising=False)
        monkeypatch.setattr(
            "unseen_university._uu_root.uu_home", lambda: str(tmp_path / "home")
        )
        assert _default_log_root() == tmp_path / "home" / "logs"


class TestWebServerNested:
    def test_web_server_log_is_under_device_dir(self):
        """The one known bespoke flat-file writer is nested under logs/web_server/,
        not a flat logs/web_server.log."""
        from unseen_university.devices.web_server import device as ws

        log_file = Path(ws._LOG_FILE)
        assert log_file.parent.name == "web_server", f"not nested: {log_file}"
        assert log_file.parent.parent.name == "logs", f"not under logs/: {log_file}"
