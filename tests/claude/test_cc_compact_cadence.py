"""Tests for cc_compact_cadence — the Stop hook that fires /autocompact every N closes."""

from __future__ import annotations

from unittest.mock import patch


class TestCountAndBaseline:
    def test_count_closes_absent_log(self, tmp_path):
        from unseen_university.devices.claude import cc_compact_cadence as m

        assert m.count_closes(tmp_path / "nope.log") == 0

    def test_count_closes_ignores_blank_lines(self, tmp_path):
        from unseen_university.devices.claude import cc_compact_cadence as m

        log = tmp_path / "sprint_tokens.log"
        log.write_text("a|T-1|...\n\nb|T-2|...\n  \nc|T-3|...\n")
        assert m.count_closes(log) == 3

    def test_baseline_roundtrip(self, tmp_path):
        from unseen_university.devices.claude import cc_compact_cadence as m

        bp = tmp_path / "compact_baseline.txt"
        m.write_baseline(bp, 7)
        assert m.read_baseline(bp) == 7

    def test_read_baseline_absent_is_zero(self, tmp_path):
        from unseen_university.devices.claude import cc_compact_cadence as m

        assert m.read_baseline(tmp_path / "nope.txt") == 0


class TestShouldCompact:
    def test_below_threshold(self):
        from unseen_university.devices.claude.cc_compact_cadence import should_compact

        assert should_compact(current=4, baseline=0, every_n=5) is False

    def test_at_threshold(self):
        from unseen_university.devices.claude.cc_compact_cadence import should_compact

        assert should_compact(current=5, baseline=0, every_n=5) is True

    def test_offset_baseline(self):
        from unseen_university.devices.claude.cc_compact_cadence import should_compact

        assert should_compact(current=12, baseline=10, every_n=5) is False
        assert should_compact(current=15, baseline=10, every_n=5) is True

    def test_zero_n_never_fires(self):
        from unseen_university.devices.claude.cc_compact_cadence import should_compact

        assert should_compact(current=100, baseline=0, every_n=0) is False


class TestMain:
    def _setup(self, tmp_path, monkeypatch, closes: int, baseline: int | None):
        from unseen_university.devices.claude import cc_compact_cadence as m

        log = tmp_path / "sprint_tokens.log"
        log.write_text("".join(f"line{i}\n" for i in range(closes)))
        bp = tmp_path / "compact_baseline.txt"
        if baseline is not None:
            bp.write_text(str(baseline))
        monkeypatch.setattr(m, "sprint_tokens_log_path", lambda: log)
        monkeypatch.setattr(m, "compact_baseline_path", lambda: bp)
        monkeypatch.setattr(m, "COMPACT_EVERY_N", 5)
        return m, log, bp

    def test_first_run_anchors_baseline_no_fire(self, tmp_path, monkeypatch):
        # No baseline file yet, 9 historical closes — should anchor, not fire.
        m, log, bp = self._setup(tmp_path, monkeypatch, closes=9, baseline=None)
        with patch.object(m, "inject_autocompact") as inj, \
             patch.object(m, "_tmux_session_exists", return_value=True):
            m.main()
        inj.assert_not_called()
        assert m.read_baseline(bp) == 9

    def test_below_threshold_no_fire(self, tmp_path, monkeypatch):
        m, log, bp = self._setup(tmp_path, monkeypatch, closes=13, baseline=10)
        with patch.object(m, "inject_autocompact") as inj, \
             patch.object(m, "_tmux_session_exists", return_value=True):
            m.main()
        inj.assert_not_called()
        assert m.read_baseline(bp) == 10  # unchanged

    def test_at_threshold_fires_and_updates_baseline(self, tmp_path, monkeypatch):
        m, log, bp = self._setup(tmp_path, monkeypatch, closes=15, baseline=10)
        with patch.object(m, "inject_autocompact") as inj, \
             patch.object(m, "_tmux_session_exists", return_value=True):
            m.main()
        inj.assert_called_once()
        assert m.read_baseline(bp) == 15  # advanced to current

    def test_no_double_fire_on_compaction_turn(self, tmp_path, monkeypatch):
        # First run fires at 15. The compaction turn closes no ticket, so a
        # second run with the same count must NOT fire again.
        m, log, bp = self._setup(tmp_path, monkeypatch, closes=15, baseline=10)
        with patch.object(m, "inject_autocompact") as inj, \
             patch.object(m, "_tmux_session_exists", return_value=True):
            m.main()
            m.main()
        assert inj.call_count == 1

    def test_no_inject_when_tmux_absent(self, tmp_path, monkeypatch):
        m, log, bp = self._setup(tmp_path, monkeypatch, closes=15, baseline=10)
        with patch.object(m, "inject_autocompact") as inj, \
             patch.object(m, "_tmux_session_exists", return_value=False):
            m.main()
        # Baseline still advances (we decided to compact); inject just skipped.
        inj.assert_not_called()
        assert m.read_baseline(bp) == 15


class TestShimStopHookRegistration:
    def test_register_then_remove_preserves_other_stop_hooks(self):
        from unseen_university.devices.claude import shim

        settings = {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "other-hook.sh"}]},
                ]
            }
        }
        shim._register_stop_hook(settings)
        assert shim._stop_hook_registered(settings) is True
        # other hook still present
        assert any(
            h.get("command") == "other-hook.sh"
            for entry in settings["hooks"]["Stop"]
            for h in entry["hooks"]
        )
        shim._remove_stop_hook(settings)
        assert shim._stop_hook_registered(settings) is False
        # other hook survived removal
        assert any(
            h.get("command") == "other-hook.sh"
            for entry in settings["hooks"]["Stop"]
            for h in entry["hooks"]
        )

    def test_register_is_idempotent(self):
        from unseen_university.devices.claude import shim

        settings: dict = {}
        shim._register_stop_hook(settings)
        shim._register_stop_hook(settings)
        count = sum(
            1
            for entry in settings["hooks"]["Stop"]
            for h in entry["hooks"]
            if h.get("id") == shim._STOP_HOOK_ID
        )
        assert count == 1
