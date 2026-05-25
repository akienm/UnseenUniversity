"""
test_twm_boot_singletons.py — T-twm-boot-singletons-replace-not-append

Boot-singleton observations (MACHINES_JSON, BOOT_SEQUENCE, STATE_INVENTORY)
are semantically one-per-instance — there's only one "current machines state,"
one "current boot orientation," one "current state inventory." Every push of
these sources must evict the prior row, not append. Without this, each boot
stacks rows at salience 0.6-1.0, flooring TWM with content that can never be
displaced by new cognitive observations.

Tests:
  - MachinesWatcher.push calls twm_evict_source before twm_push
  - MachinesWatcher.push evicts on both initial_load and file_changed paths
  - cortex.twm_evict_source method exists (signature smoke-test)
  - main.py boot_sequence and boot_state_inventory sites call twm_evict_source
    (regression guard — they're inline, harder to unit-test directly)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.push_sources import MachinesWatcher  # noqa: E402

# ── MachinesWatcher evicts before push ───────────────────────────────────────


def test_machines_watcher_evicts_before_push_on_initial_load(tmp_path):
    """First run reads MACHINES_JSON and pushes — must evict prior first."""
    machines_json = tmp_path / "machines.json"
    machines_json.write_text('{"test": "content"}')

    watcher = MachinesWatcher()
    call_order = []

    cortex = MagicMock()
    cortex.twm_evict_source.side_effect = lambda s: call_order.append(("evict", s))
    cortex.twm_push.side_effect = (
        lambda **kw: call_order.append(("push", kw["source"])) or 1
    )

    with patch("devices.igor.cognition.push_sources.MACHINES_JSON", machines_json):
        result = watcher.push(cortex)

    assert result == [1]
    # Evict must precede push (order matters — otherwise fresh row gets evicted).
    assert call_order == [("evict", "machines_watcher"), ("push", "machines_watcher")]


def test_machines_watcher_evicts_before_push_on_file_change(tmp_path):
    """Second push (file_changed path) also evicts prior — not just initial."""
    machines_json = tmp_path / "machines.json"
    machines_json.write_text('{"test": "v1"}')

    watcher = MachinesWatcher()
    watcher.CHECK_INTERVAL_SEC = 0  # no throttle for test

    cortex = MagicMock()
    cortex.twm_push.return_value = 1

    with patch("devices.igor.cognition.push_sources.MACHINES_JSON", machines_json):
        watcher.push(cortex)

        machines_json.write_text('{"test": "v2-changed"}')
        import os

        os.utime(machines_json, (123456790, 123456790))
        watcher.push(cortex)

    # Two pushes → two evicts.
    assert cortex.twm_evict_source.call_count == 2
    cortex.twm_evict_source.assert_called_with("machines_watcher")


# ── cortex method exists ─────────────────────────────────────────────────────


def test_cortex_exposes_twm_evict_source():
    """twm_evict_source must exist as a callable method on Cortex — the
    boot-singleton call sites depend on it."""
    from devices.igor.memory.cortex import Cortex

    assert hasattr(Cortex, "twm_evict_source")
    assert callable(Cortex.twm_evict_source)


# ── Regression guard for inline main.py call sites ───────────────────────────


def test_main_py_boot_sequence_calls_evict_source():
    """main.py's boot_sequence push site must invoke twm_evict_source.
    Inline code is hard to unit-test without booting full Igor, so this
    is a textual regression guard — catches anyone removing the evict call.
    """
    main_py = Path(__file__).resolve().parent.parent / "devices/igor/main.py"
    src = main_py.read_text()
    assert 'twm_evict_source("boot_sequence")' in src, (
        "main.py must call twm_evict_source('boot_sequence') before "
        "twm_push(source='boot_sequence') — T-twm-boot-singletons-replace-not-append"
    )


def test_main_py_boot_state_inventory_calls_evict_source():
    """main.py's boot_state_inventory push site must invoke twm_evict_source."""
    main_py = Path(__file__).resolve().parent.parent / "devices/igor/main.py"
    src = main_py.read_text()
    assert 'twm_evict_source("boot_state_inventory")' in src, (
        "main.py must call twm_evict_source('boot_state_inventory') before "
        "twm_push(source='boot_state_inventory') — T-twm-boot-singletons-replace-not-append"
    )
