"""Tests for availability cooldown extension (Gap B)."""

import time
from pathlib import Path
from unittest.mock import patch


def test_mark_unavailable_writes_cooldown_file(tmp_path):
    with patch("devices.granny.availability._AVAILABLE_DIR", tmp_path):
        from devices.granny.availability import mark_unavailable
        mark_unavailable("DickSimnel.0", cooldown_s=30)

    false_flag = tmp_path / "DickSimnel.0.available.false"
    cooldown_file = tmp_path / "DickSimnel.0.cooldown_until"
    assert false_flag.exists(), ".false flag must be present"
    assert cooldown_file.exists(), ".cooldown_until file must be written"
    expiry = float(cooldown_file.read_text().strip())
    assert expiry > time.time(), "expiry must be in the future"
    assert expiry < time.time() + 31, "expiry must be ~30s from now"


def test_mark_unavailable_without_cooldown_no_file(tmp_path):
    with patch("devices.granny.availability._AVAILABLE_DIR", tmp_path):
        from devices.granny.availability import mark_unavailable
        mark_unavailable("DickSimnel.0")

    cooldown_file = tmp_path / "DickSimnel.0.cooldown_until"
    assert not cooldown_file.exists(), "no cooldown file without cooldown_s"


def test_check_and_expire_cooldowns_before_expiry(tmp_path):
    with patch("devices.granny.availability._AVAILABLE_DIR", tmp_path):
        from devices.granny.availability import (
            check_and_expire_cooldowns,
            is_available,
            mark_unavailable,
        )
        mark_unavailable("DickSimnel.0", cooldown_s=3600)
        check_and_expire_cooldowns(["DickSimnel.0"])
        assert not is_available("DickSimnel.0"), "worker must still be unavailable before expiry"
        assert (tmp_path / "DickSimnel.0.cooldown_until").exists(), "cooldown_until must still exist"


def test_check_and_expire_cooldowns_after_expiry(tmp_path):
    with patch("devices.granny.availability._AVAILABLE_DIR", tmp_path):
        from devices.granny.availability import (
            check_and_expire_cooldowns,
            is_available,
            mark_unavailable,
        )
        # Write an already-expired timestamp directly
        (tmp_path / "DickSimnel.0.cooldown_until").write_text(str(time.time() - 1))
        (tmp_path / "DickSimnel.0.available.false").touch()
        # Ensure .true is absent (is_available checks both)
        (tmp_path / "DickSimnel.0.available.true").unlink(missing_ok=True)

        check_and_expire_cooldowns(["DickSimnel.0"])

        assert is_available("DickSimnel.0"), "worker must be available after cooldown expires"
        assert not (tmp_path / "DickSimnel.0.cooldown_until").exists(), "cooldown_until must be removed"


def test_clear_worker_state_removes_cooldown_file(tmp_path):
    with patch("devices.granny.availability._AVAILABLE_DIR", tmp_path):
        from devices.granny.availability import clear_worker_state, mark_unavailable
        mark_unavailable("DickSimnel.0", cooldown_s=60)
        clear_worker_state("DickSimnel.0")

    cooldown_file = tmp_path / "DickSimnel.0.cooldown_until"
    false_flag = tmp_path / "DickSimnel.0.available.false"
    assert not cooldown_file.exists(), "clear_worker_state must remove cooldown_until"
    assert not false_flag.exists(), "clear_worker_state must remove .false flag"
