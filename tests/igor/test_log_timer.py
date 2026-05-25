"""Tests for TimerHandle / get_timer in logging_setup.py."""

import logging
import time


def test_stop_returns_elapsed():
    from wild_igor.igor.logging_setup import get_timer

    log = logging.getLogger("test.timer")
    timer = get_timer(log, "test_op")
    elapsed = timer.stop()
    assert elapsed >= 0.0
    assert elapsed < 5.0  # sanity — test shouldn't take 5s


def test_stop_emits_log_line(caplog):
    from wild_igor.igor.logging_setup import get_timer

    log = logging.getLogger("test.timer.emit")
    with caplog.at_level(logging.DEBUG, logger="test.timer.emit"):
        timer = get_timer(log, "myop", ticket="T-abc")
        timer.stop(result="ok", tokens=99)

    assert len(caplog.records) == 1
    msg = caplog.records[0].message
    assert "name=myop" in msg
    assert "elapsed=" in msg
    assert "ticket=T-abc" in msg
    assert "result=ok" in msg
    assert "tokens=99" in msg


def test_context_kwargs_in_every_stop(caplog):
    from wild_igor.igor.logging_setup import get_timer

    log = logging.getLogger("test.timer.ctx")
    with caplog.at_level(logging.DEBUG, logger="test.timer.ctx"):
        timer = get_timer(log, "step", phase="observe")
        timer.stop(files=3)

    msg = caplog.records[0].message
    assert "phase=observe" in msg
    assert "files=3" in msg


def test_level_respected(caplog):
    from wild_igor.igor.logging_setup import get_timer

    log = logging.getLogger("test.timer.level")
    with caplog.at_level(logging.INFO, logger="test.timer.level"):
        timer_debug = get_timer(log, "debug_op", level=logging.DEBUG)
        timer_debug.stop()
        timer_info = get_timer(log, "info_op", level=logging.INFO)
        timer_info.stop()

    names = [r.message.split()[0] for r in caplog.records]
    assert "name=debug_op" not in names
    assert any("name=info_op" in r.message for r in caplog.records)


def test_igor_base_self_log_get_timer(caplog):
    """self.log.get_timer() works from any IgorBase subclass."""
    from wild_igor.igor.igor_base import IgorBase

    class MyComponent(IgorBase):
        def do_work(self):
            timer = self.log.get_timer("mycomp.step", level=logging.INFO, job="x")
            timer.stop(rows=7)

    comp = MyComponent()
    with caplog.at_level(logging.INFO):
        comp.do_work()

    assert any(
        "name=mycomp.step" in r.message
        and "job=x" in r.message
        and "rows=7" in r.message
        for r in caplog.records
    )


def test_elapsed_increases_with_sleep():
    from wild_igor.igor.logging_setup import get_timer

    log = logging.getLogger("test.timer.sleep")
    timer = get_timer(log, "slow_op")
    time.sleep(0.05)
    elapsed = timer.stop()
    assert elapsed >= 0.04
