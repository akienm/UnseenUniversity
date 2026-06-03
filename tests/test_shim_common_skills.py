"""Tests for BaseShim common skill interface (T-shim-common-skills).

Covers:
  - handle_command routes /help /health /stop /resume /feed
  - Unknown /verb returns helpful message
  - Non-skill input routes to _handle_non_skill
  - ScrapsShim._handle_non_skill returns a bark
  - NannyShim._handle_non_skill talks about schedule
  - BaseShim._tokenize returns token int list
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── Minimal concrete shim for testing BaseShim directly ──────────────────────

from unseen_university.shim import BaseShim


class _StubShim(BaseShim):
    """Minimal BaseShim concrete subclass for unit tests."""

    _device_id = "stub"
    _health_cache_store: dict = {}

    @property
    def device_id(self):
        return self._device_id

    def start(self):
        return True

    def stop(self):
        return True

    def restart(self):
        return True

    def self_test(self):
        return {"passed": True, "details": "stub"}

    def rollback(self):
        pass

    def health_surface(self):
        return dict(self._health_cache_store)


# ── handle_command routing ────────────────────────────────────────────────────


def test_help_returns_skill_list():
    s = _StubShim()
    out = s.handle_command("/help")
    for verb in ["/help", "/health", "/stop", "/resume", "/feed"]:
        assert verb in out


def test_health_returns_no_data_when_empty():
    s = _StubShim()
    out = s.handle_command("/health")
    assert "no health data" in out or "stub" in out


def test_health_returns_surface_values():
    s = _StubShim()
    s._health_cache_store = {"status": "ok", "uptime": "42s"}
    out = s.handle_command("/health")
    assert "status=ok" in out
    assert "uptime=42s" in out


def test_stop_calls_stop_and_reports():
    s = _StubShim()
    out = s.handle_command("/stop")
    assert "stopped" in out.lower()


def test_resume_calls_start_and_reports():
    s = _StubShim()
    out = s.handle_command("/resume")
    assert "resumed" in out.lower()


def test_feed_returns_no_entries():
    s = _StubShim()
    out = s.handle_command("/feed")
    assert "feed" in out.lower() or "no" in out.lower()


def test_unknown_verb_lists_known_skills():
    s = _StubShim()
    out = s.handle_command("/frobnicate")
    assert "/help" in out


def test_non_skill_input_routes_to_handle_non_skill():
    s = _StubShim()
    out = s.handle_command("hello there")
    assert "not a skill" in out.lower() or "help" in out.lower()


def test_handle_command_strips_whitespace():
    s = _StubShim()
    out = s.handle_command("  /help  ")
    assert "/help" in out


# ── _tokenize ─────────────────────────────────────────────────────────────────


def test_tokenize_returns_list_of_ints():
    result = _StubShim._tokenize("abc")
    assert result == [97, 98, 99]


def test_tokenize_empty_string():
    assert _StubShim._tokenize("") == []


def test_tokenize_preserves_length():
    text = "hello world"
    assert len(_StubShim._tokenize(text)) == len(text)


# ── ScrapsShim barks ──────────────────────────────────────────────────────────


def test_scraps_non_skill_returns_bark():
    from devices.scraps.shim import ScrapsShim

    s = ScrapsShim()
    out = s.handle_command("tell me a joke")
    assert out in {"Woof!", "Grr!", "Bark!", "Yip!", "Ruff!"}


def test_scraps_skill_commands_still_work():
    from devices.scraps.shim import ScrapsShim

    s = ScrapsShim()
    out = s.handle_command("/help")
    assert "/help" in out


def test_scraps_bark_is_deterministic():
    from devices.scraps.shim import ScrapsShim

    s = ScrapsShim()
    assert s.handle_command("same text") == s.handle_command("same text")


# ── NannyShim ─────────────────────────────────────────────────────────────────


def test_nanny_shim_self_test_passes():
    from devices.nanny.shim import NannyShim

    s = NannyShim()
    result = s.self_test()
    assert result["passed"] is True
    assert "schedule entries" in result["details"]


def test_nanny_non_skill_mentions_schedule():
    from devices.nanny.shim import NannyShim

    s = NannyShim()
    out = s.handle_command("what are you doing?")
    assert "nanny" in out.lower() or "schedule" in out.lower() or "help" in out.lower()


def test_nanny_skill_commands_work():
    from devices.nanny.shim import NannyShim

    s = NannyShim()
    out = s.handle_command("/help")
    assert "/help" in out
