"""Behavioral test — Granny never spawns a worker via subprocess.Popen.

Proof that the _launch_builder machinery has been removed: run_once() with
sprint tickets and an unavailable DickSimnel.0 must NOT call subprocess.Popen
(which was the only launch entry point before removal).
"""

from pathlib import Path
from unittest.mock import patch, MagicMock


def test_granny_never_spawns_a_down_worker():
    """Granny must NOT launch a worker via Popen when dispatch=bus.

    Covers the removed _launch_builder path: even with sprint tickets waiting
    and the target worker unavailable, Popen must never fire (the old launch
    block would have). Bus dispatch IS the wake — no spawn.
    """
    from unseen_university.devices.granny import daemon

    # Minimal config: DickSimnel.0 is the only worker, dispatch=bus routing.
    config = {
        "workers": {
            "DickSimnel.0": {
                "dispatch": "bus",
                "mailbox": "dicksimnel.0",
                "worker_name": "dicksimnel",
                # launch_cmd present so the OLD launch block WOULD spawn — the
                # discriminator: this test is red iff the launch machinery survives.
                "launch_cmd": "echo would-launch",
            }
        },
        "rules": [
            {"when": {"role_in": ["builder"]}, "route_to": "DickSimnel.0"},
            {"route_to": "CC.1"},  # fallback
        ],
        "granny_mailbox": "granny.0",
    }

    # Mock ticket store: one sprint ticket with builder role.
    def mock_sprint_tickets():
        return [
            {
                "id": "T-test",
                "status": "sprint",
                "role": "builder",
                "title": "test",
                "tags": [],
            }
        ]

    def mock_cleared_gated():
        return []

    def mock_handshake(*a, **k):
        return 0

    def mock_escalate(*a, **k):
        return 0

    def mock_reset_stale(*a, **k):
        return 0

    # Mock availability: DickSimnel.0 is DOWN (not available).
    def mock_is_available(wid, *a, **k):
        return False

    # Neutralize the workflow executor in case it tries to run.
    def mock_get_executor():
        executor = MagicMock()
        executor.tick = MagicMock()
        return executor

    # Patch all dependencies.
    with patch.object(daemon, "_sprint_tickets", side_effect=mock_sprint_tickets), \
         patch.object(daemon, "_cleared_gated_tickets", side_effect=mock_cleared_gated), \
         patch.object(daemon, "_process_handshake_replies", side_effect=mock_handshake), \
         patch.object(daemon, "_escalate_stale_dispatched", side_effect=mock_escalate), \
         patch.object(daemon, "_reset_stale_inprogress", side_effect=mock_reset_stale), \
         patch("unseen_university.devices.granny.availability.is_available", side_effect=mock_is_available), \
         patch("unseen_university.devices.granny.availability.check_and_expire_cooldowns"), \
         patch("unseen_university.devices.granny.availability._avail_dir", return_value=Path("/tmp/granny_no_launch_test_nonexistent")), \
         patch("unseen_university.devices.granny.daemon.subprocess.Popen") as mock_popen:
        # Call run_once with no IMAP (so bus dispatch is skipped but the ticket loop still runs).
        daemon.run_once(config, imap=None)

    # The assertion: Popen must NEVER be called.
    # (If _launch_builder were still in place, it would call Popen here.)
    mock_popen.assert_not_called()
