"""Tests for devices/nanny/cron_backend.py — OS cron abstraction."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from unseen_university.devices.nanny.cron_backend import CronJob, LinuxCronBackend, get_cron_backend


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_backend(crontab_text: str) -> LinuxCronBackend:
    """Return a LinuxCronBackend whose crontab reads from the given text."""
    lines = crontab_text.splitlines()
    b = LinuxCronBackend()
    b._read_crontab = lambda: list(lines)
    b._write_crontab = lambda ls: None  # no-op
    return b


# ── list_jobs ──────────────────────────────────────────────────────────────────

def test_list_jobs_empty_crontab():
    b = _make_backend("")
    assert b.list_jobs() == []


def test_list_jobs_basic():
    b = _make_backend("0 3 * * * /usr/bin/backup.sh\n30 1 * * * echo hello\n")
    jobs = b.list_jobs()
    assert len(jobs) == 2
    assert jobs[0].job_id == "1"
    assert jobs[0].expr == "0 3 * * *"
    assert jobs[0].cmd == "/usr/bin/backup.sh"
    assert jobs[0].enabled is True
    assert jobs[1].job_id == "2"
    assert jobs[1].expr == "30 1 * * *"
    assert jobs[1].cmd == "echo hello"


def test_list_jobs_skips_comments_and_blank_lines():
    crontab = "# this is a comment\n\n0 2 * * * do_thing\n"
    b = _make_backend(crontab)
    jobs = b.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == "1"


def test_list_jobs_disabled_line_shown_as_disabled():
    crontab = "#NANNY_DISABLED:0 4 * * * /usr/bin/myjob\n"
    b = _make_backend(crontab)
    jobs = b.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].enabled is False
    assert jobs[0].expr == "0 4 * * *"
    assert jobs[0].cmd == "/usr/bin/myjob"


def test_list_jobs_mixed_enabled_and_disabled():
    crontab = (
        "0 1 * * * active_job\n"
        "#NANNY_DISABLED:0 2 * * * paused_job\n"
        "0 3 * * * another_active\n"
    )
    b = _make_backend(crontab)
    jobs = b.list_jobs()
    assert len(jobs) == 3
    assert jobs[0].enabled is True
    assert jobs[1].enabled is False
    assert jobs[2].enabled is True


# ── add_job ────────────────────────────────────────────────────────────────────

def test_add_job_appends_to_crontab():
    written = []

    b = LinuxCronBackend()
    b._read_crontab = lambda: ["0 1 * * * existing_job"]
    b._write_crontab = lambda ls: written.extend(ls)

    # After write we re-read from the written lines
    def _re_read():
        return written if written else ["0 1 * * * existing_job"]
    b._read_crontab = _re_read

    job = b.add_job("* * * * *", "echo test")
    assert "* * * * * echo test" in written
    assert job.expr == "* * * * *"
    assert job.cmd == "echo test"
    assert job.enabled is True


# ── disable_job ────────────────────────────────────────────────────────────────

def test_disable_job_comments_out_line():
    original = "0 5 * * * /bin/backup"
    written = []

    b = LinuxCronBackend()
    b._read_crontab = lambda: [original]
    b._write_crontab = lambda ls: written.extend(ls)

    ok = b.disable_job("1")
    assert ok is True
    assert len(written) == 1
    assert written[0] == f"#NANNY_DISABLED:{original}"


def test_disable_job_returns_false_for_unknown_id():
    b = _make_backend("0 1 * * * some_job\n")
    assert b.disable_job("99") is False


def test_disable_job_already_disabled_returns_false():
    crontab = "#NANNY_DISABLED:0 4 * * * /usr/bin/myjob"
    b = _make_backend(crontab)
    assert b.disable_job("1") is False


# ── enable_job ─────────────────────────────────────────────────────────────────

def test_enable_job_uncomments_line():
    original = "0 6 * * * /bin/do_thing"
    disabled = f"#NANNY_DISABLED:{original}"
    written = []

    b = LinuxCronBackend()
    b._read_crontab = lambda: [disabled]
    b._write_crontab = lambda ls: written.extend(ls)

    ok = b.enable_job("1")
    assert ok is True
    assert written[0] == original


def test_enable_job_returns_false_for_enabled_job():
    b = _make_backend("0 7 * * * already_active\n")
    assert b.enable_job("1") is False


def test_enable_job_returns_false_for_unknown_id():
    b = _make_backend("#NANNY_DISABLED:0 8 * * * hidden\n")
    assert b.enable_job("99") is False


# ── run_now ────────────────────────────────────────────────────────────────────

def test_run_now_returns_none_for_unknown_id():
    b = _make_backend("0 1 * * * echo hi\n")
    assert b.run_now("99") is None


def test_run_now_executes_job():
    b = _make_backend("* * * * * echo nanny_test_output\n")
    result = b.run_now("1")
    assert result is not None
    assert result.returncode == 0
    assert "nanny_test_output" in result.stdout


# ── get_cron_backend ───────────────────────────────────────────────────────────

def test_get_cron_backend_returns_linux_on_linux():
    with patch("platform.system", return_value="Linux"):
        backend = get_cron_backend()
        assert isinstance(backend, LinuxCronBackend)


def test_get_cron_backend_windows_raises_not_implemented():
    from unseen_university.devices.nanny.cron_backend import WindowsCronBackend
    with patch("platform.system", return_value="Windows"):
        backend = get_cron_backend()
        assert isinstance(backend, WindowsCronBackend)
        with pytest.raises(NotImplementedError):
            backend.list_jobs()
