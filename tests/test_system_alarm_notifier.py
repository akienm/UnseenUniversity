"""Proof for T-system-alarms-tmux-nag.

The out-of-band notifier nags ONCE per new/reopened alarm, never per increment,
and is a silent no-op when no tmux session is reachable. Uses an injected
send_fn so the live tmux path isn't exercised.
"""

from __future__ import annotations

import pytest

from unseen_university import system_alarms as sa
from unseen_university import system_alarm_notifier as nf


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    monkeypatch.setenv("IGOR_HOME", str(tmp_path))
    monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
    return tmp_path


def test_new_alarm_nags_once_then_silent():
    sa.raise_alarm("no-provider:worker", "caller.a", "down", emit_log=False)
    calls: list[str] = []
    ok = lambda s: (calls.append(s) or True)  # noqa: E731

    assert nf.notify_new_alarms(send_fn=ok) == 1
    assert len(calls) == 1
    assert "uu alarms" in calls[0]
    # second sweep: already stamped → no re-nag
    assert nf.notify_new_alarms(send_fn=ok) == 0
    assert len(calls) == 1


def test_increment_does_not_renag():
    sa.raise_alarm("no-provider:worker", "caller.a", "down", emit_log=False)
    nf.notify_new_alarms(send_fn=lambda s: True)
    sa.raise_alarm("no-provider:worker", "caller.a", "down", emit_log=False)  # increment
    calls: list[str] = []
    assert nf.notify_new_alarms(send_fn=lambda s: (calls.append(s) or True)) == 0
    assert calls == []


def test_reopen_renags():
    sa.raise_alarm("no-provider:analyst", "caller.b", "down", emit_log=False)
    nf.notify_new_alarms(send_fn=lambda s: True)
    sa.close_alarm("no-provider:analyst")
    sa.raise_alarm("no-provider:analyst", "caller.b", "again", emit_log=False)  # reopened
    calls: list[str] = []
    assert nf.notify_new_alarms(send_fn=lambda s: (calls.append(s) or True)) == 1
    assert len(calls) == 1


def test_no_tmux_is_silent_noop():
    """Send failure (no session) → no nag, no stamp, no raise; retries next sweep."""
    sa.raise_alarm("no-provider:minion", "caller.c", "down", emit_log=False)
    assert nf.notify_new_alarms(send_fn=lambda s: False) == 0
    assert sa.get_alarm("no-provider:minion").get("notified_at") is None
