"""Tests for stale_slate_check.py — covers date detection + open-items detection."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from devlab.claudecode import stale_slate_check as mod


@pytest.fixture
def slate_dir(tmp_path: Path) -> Path:
    d = tmp_path / "claudecode"
    d.mkdir()
    return d


def _write_slate(slate_dir: Path, yyyymmdd: str, content: str) -> Path:
    p = slate_dir / f"{yyyymmdd}.slate.txt"
    p.write_text(content)
    return p


def test_no_slates_returns_none(slate_dir: Path):
    assert mod.find_latest_slate_before(date(2026, 4, 22), slate_dir) is None


def test_ignores_slates_from_today_and_future(slate_dir: Path):
    _write_slate(slate_dir, "20260422", "# today\n")
    _write_slate(slate_dir, "20260423", "# future\n")
    assert mod.find_latest_slate_before(date(2026, 4, 22), slate_dir) is None


def test_returns_newest_before_today(slate_dir: Path):
    _write_slate(slate_dir, "20260419", "# old\n")
    _write_slate(slate_dir, "20260420", "# older-today\n")
    _write_slate(slate_dir, "20260421", "# yesterday\n")
    _write_slate(slate_dir, "20260422", "# today\n")
    found = mod.find_latest_slate_before(date(2026, 4, 22), slate_dir)
    assert found is not None
    assert found.name == "20260421.slate.txt"


def test_ignores_non_slate_filenames(slate_dir: Path):
    (slate_dir / "notes.txt").write_text("hi")
    (slate_dir / "20260421.log").write_text("hi")
    assert mod.find_latest_slate_before(date(2026, 4, 22), slate_dir) is None


def test_closed_marker_wins_even_with_content(tmp_path: Path):
    p = tmp_path / "s.txt"
    p.write_text(
        "# Slate\n\n## Next up\n- something\n\n## Done ✅ CLOSED\n- all done\n"
    )
    assert mod.slate_has_open_items(p) is False


def test_empty_next_up_blocked_after_that_is_closed(tmp_path: Path):
    p = tmp_path / "s.txt"
    p.write_text(
        "# Slate\n\n## Next up\n\n## Blocked\n\n## After that\n\n## Done today\n- x\n"
    )
    assert mod.slate_has_open_items(p) is False


def test_content_in_next_up_is_open(tmp_path: Path):
    p = tmp_path / "s.txt"
    p.write_text("# Slate\n\n## Next up\n- T-something\n\n## Blocked\n\n")
    assert mod.slate_has_open_items(p) is True


def test_content_in_blocked_is_open(tmp_path: Path):
    p = tmp_path / "s.txt"
    p.write_text("# Slate\n\n## Next up\n\n## Blocked\n- T-waiting-on-igor\n")
    assert mod.slate_has_open_items(p) is True


def test_content_in_after_that_is_open(tmp_path: Path):
    p = tmp_path / "s.txt"
    p.write_text("# Slate\n\n## Next up\n\n## Blocked\n\n## After that\n- T-later\n")
    assert mod.slate_has_open_items(p) is True


def test_done_section_content_does_not_count_as_open(tmp_path: Path):
    p = tmp_path / "s.txt"
    p.write_text(
        "# Slate\n\n## Next up\n\n## Blocked\n\n## After that\n\n## Done\n- T-shipped\n"
    )
    assert mod.slate_has_open_items(p) is False


def test_format_slate_date():
    assert mod.format_slate_date("20260421.slate.txt") == "2026-04-21"


# ── JSON slate format ─────────────────────────────────────────────────────────

import json as _json


def _write_json_slate(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "20260421.slate.txt"
    p.write_text(_json.dumps(data))
    return p


def test_json_slate_empty_is_not_open(tmp_path: Path):
    p = _write_json_slate(tmp_path, {"date": "2026-04-21", "in_flight": [], "planned": [], "done": [], "notes": []})
    assert mod.slate_has_open_items(p) is False


def test_json_slate_with_in_flight_is_open(tmp_path: Path):
    p = _write_json_slate(tmp_path, {"date": "2026-04-21", "in_flight": ["T-something in progress"], "planned": []})
    assert mod.slate_has_open_items(p) is True


def test_json_slate_with_planned_is_open(tmp_path: Path):
    p = _write_json_slate(tmp_path, {"date": "2026-04-21", "in_flight": [], "planned": ["T-next-up"]})
    assert mod.slate_has_open_items(p) is True


def test_json_slate_closed_flag_wins(tmp_path: Path):
    p = _write_json_slate(tmp_path, {"date": "2026-04-21", "in_flight": ["still here"], "planned": ["more"], "closed": True})
    assert mod.slate_has_open_items(p) is False


def test_json_slate_done_only_is_not_open(tmp_path: Path):
    p = _write_json_slate(tmp_path, {"date": "2026-04-21", "in_flight": [], "planned": [], "done": ["T-shipped"]})
    assert mod.slate_has_open_items(p) is False
