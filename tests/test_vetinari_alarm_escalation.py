"""Tests for VetinariDevice system alarm escalation (T-vetinari-owns-alarm-escalation).

Completion criteria:
- VetinariDevice.sweep_system_alarms() escalates new/reopened alarms once via _escalate_alarm
- Reopened alarms are not re-escalated if already notified
- Failed escalations leave the alarm unnotified (so later sweeps retry)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch


def _make_vetinari_with_spy(tmp_path):
    """Build a VetinariDevice with a spy channel_post function.

    Returns (device, channel_calls_list, escalation_spy_list).
    """
    import unseen_university.devices.vetinari.device as _vd

    _vd.uu_home = lambda p=str(tmp_path): p

    from unseen_university.devices.vetinari.device import VetinariDevice

    channel_calls = []
    escalation_calls = []

    def spy_channel_post(msg):
        channel_calls.append(msg)

    v = VetinariDevice(channel_post_fn=spy_channel_post)

    # Monkeypatch _escalate_alarm to spy on it
    original_escalate = v._escalate_alarm

    def spy_escalate(summary):
        escalation_calls.append(summary)
        return original_escalate(summary)

    v._escalate_alarm = spy_escalate

    return v, channel_calls, escalation_calls


class TestSweepEscalatesOpenAlarmOnce:
    """Seed one new alarm; sweep; assert spy called once + alarm notified."""

    def test_sweep_escalates_open_alarm_once(self, tmp_path):
        import unseen_university.system_alarms as sa

        # Redirect system_alarms uu_home to tmp_path
        with patch("unseen_university.system_alarms.uu_home", lambda: str(tmp_path)):
            # Redirect vetinari uu_home to tmp_path
            with patch(
                "unseen_university.devices.vetinari.device.uu_home", lambda: str(tmp_path)
            ):
                v, channel_calls, escalation_calls = _make_vetinari_with_spy(tmp_path)

                # Seed one alarm
                sa.raise_alarm("test-alarm:demo", "test.caller", "demo alarm", emit_log=False)

                # Sweep
                nagged = v.sweep_system_alarms()

                # Assert: one alarm escalated
                assert nagged == 1
                assert len(escalation_calls) == 1
                assert "SYSTEM ALARM: test-alarm:demo" in escalation_calls[0]
                assert "run: uu alarms" in escalation_calls[0]

                # Assert: channel_post was called
                assert len(channel_calls) == 1
                assert "SYSTEM ALARM: test-alarm:demo" in channel_calls[0]

                # Assert: alarm now has notified_at
                rec = sa.get_alarm("test-alarm:demo")
                assert rec is not None
                assert rec.get("notified_at") is not None

    def test_reopened_alarm_escalates(self, tmp_path):
        import unseen_university.system_alarms as sa

        with patch("unseen_university.system_alarms.uu_home", lambda: str(tmp_path)):
            with patch(
                "unseen_university.devices.vetinari.device.uu_home", lambda: str(tmp_path)
            ):
                v, channel_calls, escalation_calls = _make_vetinari_with_spy(tmp_path)

                # Seed and close an alarm
                sa.raise_alarm("reopened-test:sig", "test.caller", "initial", emit_log=False)
                sa.close_alarm("reopened-test:sig")

                # Reopen it
                sa.raise_alarm("reopened-test:sig", "test.caller", "reopened", emit_log=False)

                # Sweep — should escalate (reopened alarms have no notified_at)
                nagged = v.sweep_system_alarms()
                assert nagged == 1
                assert len(escalation_calls) == 1
                assert "reopened-test:sig" in escalation_calls[0]


class TestSweepDoesNotReEscalate:
    """After first sweep, call again; assert spy not called again."""

    def test_sweep_does_not_re_escalate(self, tmp_path):
        import unseen_university.system_alarms as sa

        with patch("unseen_university.system_alarms.uu_home", lambda: str(tmp_path)):
            with patch(
                "unseen_university.devices.vetinari.device.uu_home", lambda: str(tmp_path)
            ):
                v, channel_calls, escalation_calls = _make_vetinari_with_spy(tmp_path)

                # Seed one alarm
                sa.raise_alarm("once-only:test", "test.caller", "demo", emit_log=False)

                # First sweep
                nagged1 = v.sweep_system_alarms()
                assert nagged1 == 1
                assert len(escalation_calls) == 1

                # Second sweep — should not escalate
                nagged2 = v.sweep_system_alarms()
                assert nagged2 == 0
                assert len(escalation_calls) == 1  # no new calls


class TestFailedPostLeavesAlarmUnnotified:
    """Make _escalate_alarm return False; sweep; assert alarm not stamped."""

    def test_failed_post_leaves_alarm_unnotified(self, tmp_path):
        import unseen_university.system_alarms as sa

        with patch("unseen_university.system_alarms.uu_home", lambda: str(tmp_path)):
            with patch(
                "unseen_university.devices.vetinari.device.uu_home", lambda: str(tmp_path)
            ):
                v, channel_calls, escalation_calls = _make_vetinari_with_spy(tmp_path)

                # Seed one alarm
                sa.raise_alarm("failed-post:test", "test.caller", "demo", emit_log=False)

                # Make _escalate_alarm return False
                v._escalate_alarm = lambda summary: False

                # Sweep
                nagged = v.sweep_system_alarms()

                # Since _escalate_alarm returns False, notify_new_alarms does NOT
                # stamp notified_at. Count is still 0 because sender failed.
                assert nagged == 0

                # Assert: alarm is NOT notified
                rec = sa.get_alarm("failed-post:test")
                assert rec is not None
                assert rec.get("notified_at") is None

                # A later sweep with a working escalator should retry
                v._escalate_alarm = lambda summary: True
                nagged2 = v.sweep_system_alarms()
                assert nagged2 == 1

                # Now it's notified
                rec2 = sa.get_alarm("failed-post:test")
                assert rec2 is not None
                assert rec2.get("notified_at") is not None

    def test_escalate_alarm_exception_returns_false(self, tmp_path):
        """Make _channel_post raise; _escalate_alarm should catch and return False."""
        import unseen_university.system_alarms as sa

        def boom_channel_post(msg):
            raise RuntimeError("channel post boom")

        v = None
        with patch("unseen_university.system_alarms.uu_home", lambda: str(tmp_path)):
            with patch(
                "unseen_university.devices.vetinari.device.uu_home", lambda: str(tmp_path)
            ):
                from unseen_university.devices.vetinari.device import VetinariDevice

                v = VetinariDevice(channel_post_fn=boom_channel_post)

                # Seed one alarm
                sa.raise_alarm("exception-test:sig", "test.caller", "demo", emit_log=False)

                # Sweep — _escalate_alarm catches the exception and returns False
                nagged = v.sweep_system_alarms()
                assert nagged == 0

                # Alarm is not notified
                rec = sa.get_alarm("exception-test:sig")
                assert rec.get("notified_at") is None


class TestAuditLogAlarmsEscalation:
    """Verify audit log records ALARM_ESCALATE events."""

    def test_audit_log_records_alarm_escalation(self, tmp_path):
        import unseen_university.system_alarms as sa

        with patch("unseen_university.system_alarms.uu_home", lambda: str(tmp_path)):
            with patch(
                "unseen_university.devices.vetinari.device.uu_home", lambda: str(tmp_path)
            ):
                v, _, _ = _make_vetinari_with_spy(tmp_path)

                # Seed one alarm
                sa.raise_alarm("audit-test:sig", "test.caller", "message", emit_log=False)

                # Sweep
                v.sweep_system_alarms()

                # Check audit log
                entries = v.get_audit_log()
                assert len(entries) > 0
                alarm_entries = [e for e in entries if e.get("event") == "ALARM_ESCALATE"]
                assert len(alarm_entries) >= 1
                assert "new or reopened system alarm" in alarm_entries[0].get("reason", "")


class TestDefaultPathRetry:
    """The retry-on-failure contract must hold on the DEFAULT (production) channel-post
    path — not only when a raising channel_post_fn is injected (T-vetinari-alarm-retry-
    default-path). Production builds VetinariDevice() with no injected fn, so a swallowed
    channel failure must still leave the alarm un-notified so the next sweep retries."""

    def test_default_path_failed_post_leaves_alarm_unnotified(self, tmp_path):
        import unseen_university.system_alarms as sa

        def boom(_msg):
            raise RuntimeError("channel down")

        with patch("unseen_university.system_alarms.uu_home", lambda: str(tmp_path)):
            with patch(
                "unseen_university.devices.vetinari.device.uu_home", lambda: str(tmp_path)
            ):
                with patch("unseen_university.channel.post_to_channel", boom):
                    from unseen_university.devices.vetinari.device import VetinariDevice

                    # DEFAULT construction — no channel_post_fn (the production config)
                    v = VetinariDevice()
                    sa.raise_alarm("default-path:sig", "test.caller", "demo", emit_log=False)

                    nagged = v.sweep_system_alarms()

                    # Post failed on the default path → nothing escalated, alarm NOT stamped
                    assert nagged == 0
                    rec = sa.get_alarm("default-path:sig")
                    assert rec is not None
                    assert rec.get("notified_at") is None
