"""Tests for export_chat.py — cross-day routing, no-timestamp handling, idempotency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devlab.claudecode import export_chat as mod


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_local_date_of_handles_z_suffix():
    assert mod._local_date_of("2026-04-22T16:04:36.902Z") is not None


def test_local_date_of_rejects_empty():
    assert mod._local_date_of("") is None
    assert mod._local_date_of(None) is None  # type: ignore[arg-type]


def test_local_date_of_rejects_garbage():
    assert mod._local_date_of("not-a-date") is None


def test_partition_single_day_session(tmp_path: Path):
    p = tmp_path / "abc.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "user",
                "timestamp": "2026-04-22T10:00:00Z",
                "message": {"content": "hi"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-04-22T10:01:00Z",
                "message": {"content": "hello"},
            },
        ],
    )
    result = mod.partition_session_by_day(p)
    assert list(result.keys()) == [
        mod._local_date_of("2026-04-22T10:00:00Z")
    ]  # just one day


def test_partition_cross_day_session_splits(tmp_path: Path):
    """Use mid-day UTC timestamps so local-time conversion doesn't collapse dates."""
    p = tmp_path / "abc.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "user",
                "timestamp": "2026-04-20T17:00:00Z",
                "message": {"content": "day1"},
            },
            {
                "type": "user",
                "timestamp": "2026-04-21T17:00:00Z",
                "message": {"content": "day2"},
            },
            {
                "type": "user",
                "timestamp": "2026-04-22T17:00:00Z",
                "message": {"content": "day3"},
            },
        ],
    )
    result = mod.partition_session_by_day(p)
    assert len(result) == 3


def test_partition_skips_records_before_first_timestamp(tmp_path: Path):
    """Records like permission-mode / file-history-snapshot have no timestamp
    and sit before the first user/assistant turn — they must NOT anchor routing
    to today's date via mtime fallback (the original bug)."""
    p = tmp_path / "abc.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "permission-mode", "mode": "default"},
            {"type": "file-history-snapshot", "snapshot": {}},
            {
                "type": "user",
                "timestamp": "2026-04-18T17:14:51.877Z",
                "message": {"content": "first real msg"},
            },
        ],
    )
    result = mod.partition_session_by_day(p)
    dates = list(result.keys())
    assert len(dates) == 1
    assert dates[0] == mod._local_date_of("2026-04-18T17:14:51.877Z")


def test_partition_no_ts_record_attaches_to_previous_date(tmp_path: Path):
    """Per ticket spec: message without timestamp attaches to the previous
    timestamped message's day."""
    p = tmp_path / "abc.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "user",
                "timestamp": "2026-04-20T10:00:00Z",
                "message": {"content": "has ts"},
            },
            # No-ts user message — attaches to previous date.
            {"type": "user", "message": {"content": "no ts, attach to prev"}},
        ],
    )
    result = mod.partition_session_by_day(p)
    assert len(result) == 1
    day = list(result.keys())[0]
    # Both messages rendered under the same day
    assert len(result[day]) == 2


def test_partition_ignores_malformed_json(tmp_path: Path):
    p = tmp_path / "abc.jsonl"
    with p.open("w") as f:
        f.write(
            '{"type": "user", "timestamp": "2026-04-22T10:00:00Z", "message": {"content": "good"}}\n'
        )
        f.write("not-valid-json\n")
        f.write(
            '{"type": "user", "timestamp": "2026-04-22T10:01:00Z", "message": {"content": "also good"}}\n'
        )
    result = mod.partition_session_by_day(p)
    total_msgs = sum(len(v) for v in result.values())
    assert total_msgs == 2  # malformed line dropped, two good ones kept


def test_partition_ignores_blank_lines(tmp_path: Path):
    p = tmp_path / "abc.jsonl"
    with p.open("w") as f:
        f.write("\n")
        f.write(
            '{"type": "user", "timestamp": "2026-04-22T10:00:00Z", "message": {"content": "ok"}}\n'
        )
        f.write("\n")
    result = mod.partition_session_by_day(p)
    assert sum(len(v) for v in result.values()) == 1


def test_render_day_file_union_orders_sessions_deterministically():
    per_session = [
        ("zzz-session", ["\n### User — ts\n\ncontent z\n"]),
        ("aaa-session", ["\n### User — ts\n\ncontent a\n"]),
    ]
    out = mod.render_day_file("2026-04-22", per_session)
    # aaa should come before zzz in output (sorted order)
    assert out.index("aaa-session") < out.index("zzz-session")
    assert "# Chat log — 2026-04-22" in out


def test_render_day_file_empty_per_session_still_valid():
    out = mod.render_day_file("2026-04-22", [])
    assert "# Chat log — 2026-04-22" in out


def test_render_is_idempotent_ignoring_render_timestamp(tmp_path: Path):
    """Two renders of the same input produce outputs that differ only in the
    top-of-file rendered-at timestamp."""
    per_session = [
        ("abc", ["\n### User — 2026-04-22T10:00:00Z\n\nhello\n"]),
    ]
    a = mod.render_day_file("2026-04-22", per_session)
    b = mod.render_day_file("2026-04-22", per_session)

    # Strip the rendered-at line from both and compare.
    def strip_rendered_at(s: str) -> str:
        return "\n".join(
            line for line in s.splitlines() if not line.startswith("_rendered ")
        )

    assert strip_rendered_at(a) == strip_rendered_at(b)


def test_resolve_target_newest_when_no_flags(tmp_path: Path):
    d = tmp_path / "transcripts"
    d.mkdir()
    import os
    import time

    old = d / "old.jsonl"
    old.write_text("")
    time.sleep(0.01)
    new = d / "new.jsonl"
    new.write_text("")
    # Ensure mtime ordering
    os.utime(old, (time.time() - 100, time.time() - 100))
    targets = mod.resolve_target_sessions(d, None, False)
    assert len(targets) == 1
    assert targets[0].name == "new.jsonl"


def test_resolve_target_all_returns_everything(tmp_path: Path):
    d = tmp_path / "transcripts"
    d.mkdir()
    (d / "one.jsonl").write_text("")
    (d / "two.jsonl").write_text("")
    targets = mod.resolve_target_sessions(d, None, True)
    assert len(targets) == 2


def test_resolve_target_specific_session(tmp_path: Path):
    d = tmp_path / "transcripts"
    d.mkdir()
    (d / "wanted.jsonl").write_text("")
    (d / "other.jsonl").write_text("")
    targets = mod.resolve_target_sessions(d, "wanted", False)
    assert len(targets) == 1
    assert targets[0].name == "wanted.jsonl"


def test_resolve_target_specific_session_missing_exits(tmp_path: Path):
    d = tmp_path / "transcripts"
    d.mkdir()
    (d / "other.jsonl").write_text("")
    with pytest.raises(SystemExit):
        mod.resolve_target_sessions(d, "nonexistent", False)
