"""Proof for T-uu-readfeed — `uu device <dev> feed [channel]` (D-skills-two-products).

`/readigor` only ever read Igor's one channel. This generalizes to a device-verb
feed reader: any device, any of the uniform channels — personal (web-chat),
private (a deliberate not-yet-designed stub), and the three per-device log-level
streams info/warn/debug (the hierarchy T-per-device-log-hierarchy shipped).

The discriminator these tests pin: a log-level channel routes to
<log_root>/<instance>/<stream>/ and returns the records written there — in
particular `warn`, the channel that was MISSING from the original ticket and
added on 2026-06-25. The reader honors UU_LOG_ROOT, so the whole thing proves
itself against a tmp tree with no running rack. The RED state is the stubbed
reader that returns nothing; GREEN reads the stream.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "devlab" / "claudecode"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import uu_feed  # noqa: E402


def _write_record(root: Path, inst: str, stream: str, name: str, payload: dict) -> None:
    d = root / inst / stream
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload))


class TestLogChannels:
    def test_warn_channel_reads_warn_stream(self, tmp_path):
        """warn — the channel added 2026-06-25 — routes to the warn stream."""
        root = tmp_path / "logs"
        _write_record(
            root, "dev1", "warn", "20260625-100000-000001_x_warning.json",
            {"ts": "2026-06-25T10:00:00", "level": "WARNING", "message": "a warning"},
        )
        recs = uu_feed.read_log_stream("dev1", "warn", log_root=root)
        assert recs, "warn stream returned nothing"
        assert recs[-1]["message"] == "a warning"

    def test_info_and_debug_route_distinctly(self, tmp_path):
        root = tmp_path / "logs"
        _write_record(root, "dev1", "info", "20260625-100000-000001_x_info.json",
                      {"ts": "t", "level": "INFO", "message": "an info"})
        _write_record(root, "dev1", "debug", "20260625-100000-000002_x_debug.json",
                      {"ts": "t", "level": "DEBUG", "message": "a debug"})
        info = uu_feed.read_log_stream("dev1", "info", log_root=root)
        debug = uu_feed.read_log_stream("dev1", "debug", log_root=root)
        assert [r["message"] for r in info] == ["an info"]
        assert [r["message"] for r in debug] == ["a debug"]

    def test_records_ordered_oldest_to_newest(self, tmp_path):
        root = tmp_path / "logs"
        _write_record(root, "dev1", "info", "20260625-100000-000001_a_info.json",
                      {"ts": "t1", "level": "INFO", "message": "first"})
        _write_record(root, "dev1", "info", "20260625-100005-000001_b_info.json",
                      {"ts": "t2", "level": "INFO", "message": "second"})
        recs = uu_feed.read_log_stream("dev1", "info", log_root=root)
        assert [r["message"] for r in recs] == ["first", "second"]

    def test_missing_stream_is_empty_not_error(self, tmp_path):
        assert uu_feed.read_log_stream("nope", "info", log_root=tmp_path / "logs") == []


class TestInstanceResolution:
    def test_igor_resolves_to_instance_dir(self, tmp_path):
        root = tmp_path / "logs"
        (root / "Igor-wild-0001" / "info").mkdir(parents=True)
        assert uu_feed.resolve_instance("igor", root) == "Igor-wild-0001"

    def test_exact_device_dir_wins(self, tmp_path):
        root = tmp_path / "logs"
        (root / "granny" / "info").mkdir(parents=True)
        assert uu_feed.resolve_instance("granny", root) == "granny"


class TestPersonalChannel:
    def test_personal_reads_webchat_store(self, tmp_path):
        chan = tmp_path / "local" / "cc_channel"
        chan.mkdir(parents=True)
        (chan / "messages.jsonl").write_text(
            json.dumps({"ts": "t", "author": "igor", "content": "hello from igor"}) + "\n"
            + json.dumps({"ts": "t", "author": "granny", "content": "other device"}) + "\n"
        )
        recs = uu_feed.read_personal("igor", home=tmp_path)
        assert any("hello from igor" in r["content"] for r in recs)
        assert all(r.get("author") == "igor" for r in recs), "personal not scoped to device"


class TestCliContract:
    def test_default_channel_is_personal(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("UU_LOG_ROOT", str(tmp_path / "logs"))
        rc = uu_feed.main(["feed", "igor"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "feed — personal" in out

    def test_private_prints_notice_exit_zero(self, capsys):
        rc = uu_feed.main(["feed", "igor", "private"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "not yet designed" in out

    def test_unknown_channel_errors_nonzero(self, capsys):
        rc = uu_feed.main(["feed", "igor", "bogus"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "unknown channel" in err
        assert "warn" in err  # lists the valid channels

    def test_no_args_usage_nonzero(self, capsys):
        rc = uu_feed.main(["feed"])
        assert rc == 2
        assert "usage" in capsys.readouterr().err
