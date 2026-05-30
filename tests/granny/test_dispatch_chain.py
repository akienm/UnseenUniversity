"""Smoke tests: queue → Granny routing → cc_task_listener dispatch chain.

Three stages verified:

  Stage 1 — Routing edge existence:
    _DEFAULT_ROUTING contains Platform / Infrastructure / tests → cc.
    Guards against the regression where 'tests' was missing and Granny
    could not route tickets tagged with it.

  Stage 2 — route_ticket() produces GRANNY_DISPATCH:
    GrannyWeatherwaxDevice.route_ticket() posts a well-formed GRANNY_DISPATCH
    message to the shared channel for cc-routed tags. Uses the skeleton edges
    (no dispatch_fn) — the real production path when GrannyDaemon has not yet
    registered the cc worker with cc_dispatch_fn.

  Stage 3 — cc_task_listener drives sprint → in_progress (Postgres):
    A sprint ticket and a GRANNY_DISPATCH message are inserted directly into
    Postgres. TaskListener.poll_once() is called and the ticket must reach
    in_progress status.  This proves that cc_task_listener can act as the
    sole dispatch mechanism — the path that was broken when the listener was
    not started from GrannyDaemon.

NOTE: double-dispatch exists in production — cc_dispatch_fn (in dispatch.py)
calls cc_queue.py dispatch AND posts GRANNY_DISPATCH, so cc_task_listener's
dispatch call is a no-op for an already-in_progress ticket.  Stage 3 isolates
the listener's path by skipping cc_dispatch_fn entirely so the dispatch
responsibility belongs solely to the listener.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import psycopg2
import psycopg2.extras
import pytest

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_TICKET_ID = "T-granny-dispatch-smoke-tmp"


def _db_reachable() -> bool:
    try:
        conn = psycopg2.connect(_DB_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


# ── Stage 1: routing edge existence ──────────────────────────────────────────


class TestDefaultRoutingEdges:
    """_DEFAULT_ROUTING must cover the cc-routed tags.

    Missing entries here break Granny's ability to dispatch those tickets —
    they fall through to 'no_route' escalation instead of reaching CC.
    """

    def test_platform_routes_to_cc(self):
        from devices.granny.device import _DEFAULT_ROUTING

        assert "cc" in _DEFAULT_ROUTING.get("Platform", [])

    def test_infrastructure_routes_to_cc(self):
        from devices.granny.device import _DEFAULT_ROUTING

        assert "cc" in _DEFAULT_ROUTING.get("Infrastructure", [])

    def test_tests_tag_routes_to_cc(self):
        from devices.granny.device import _DEFAULT_ROUTING

        assert "cc" in _DEFAULT_ROUTING.get("tests", [])


# ── Stage 2: route_ticket → GRANNY_DISPATCH in channel ───────────────────────


class TestGrannyRoutePostsDispatch:
    """route_ticket() must post a GRANNY_DISPATCH message for cc-routed tags.

    _dispatch_to_cc posts GRANNY_DISPATCH to the channel AND spawns a claude
    subprocess.  Tests patch subprocess.Popen to prevent real spawns; they
    capture the channel message via the _post_to_channel mock and verify format.
    """

    def _ticket(self, tags: list[str], id: str = "T-smoke") -> dict:
        return {
            "id": id,
            "title": "smoke test ticket",
            "size": "S",
            "tags": tags,
            "description": (
                "**Affected files:** x.py\n"
                "**Scope boundary:** test only\n"
                "**Completion criteria:** green"
            ),
        }

    def _make_device(self):
        from devices.granny.device import GrannyWeatherwaxDevice

        # Patch _post_to_channel on the class so __init__ logging does not write
        # to the live channel (it doesn't currently, but this mirrors the project
        # test pattern and is defensive against future __init__ changes).
        with patch.object(GrannyWeatherwaxDevice, "_post_to_channel"):
            return GrannyWeatherwaxDevice()

    def test_platform_ticket_posts_granny_dispatch(self):
        device = self._make_device()
        posted: list[str] = []

        with (
            patch.object(
                device,
                "_post_to_channel",
                side_effect=lambda channel, msg: posted.append(msg),
            ),
            patch("devices.granny.device.subprocess.Popen"),
        ):
            dispatched, worker = device.route_ticket(self._ticket(["Platform"]))

        assert dispatched is True
        assert worker == "cc"
        dispatch_msgs = [m for m in posted if "GRANNY_DISPATCH" in m]
        assert (
            dispatch_msgs
        ), "route_ticket did not post GRANNY_DISPATCH for Platform tag"
        assert "T-smoke" in dispatch_msgs[0]
        assert "worker=cc" in dispatch_msgs[0]

    def test_tests_tag_posts_granny_dispatch(self):
        device = self._make_device()
        posted: list[str] = []

        with (
            patch.object(
                device,
                "_post_to_channel",
                side_effect=lambda channel, msg: posted.append(msg),
            ),
            patch("devices.granny.device.subprocess.Popen"),
        ):
            dispatched, worker = device.route_ticket(
                self._ticket(["tests"], id="T-tests-smoke")
            )

        assert dispatched is True
        assert worker == "cc"
        assert any(
            "GRANNY_DISPATCH" in m for m in posted
        ), "route_ticket did not post GRANNY_DISPATCH for 'tests' tag"

    def test_granny_dispatch_message_is_parseable(self):
        """The message Granny posts must be parseable by cc_task_listener."""
        from lab.claudecode.cc_task_listener import _parse_dispatch_msg

        device = self._make_device()
        posted: list[str] = []

        with (
            patch.object(
                device,
                "_post_to_channel",
                side_effect=lambda channel, msg: posted.append(msg),
            ),
            patch("devices.granny.device.subprocess.Popen"),
        ):
            device.route_ticket(self._ticket(["Platform"], id="T-parse-test"))

        dispatch_msgs = [m for m in posted if "GRANNY_DISPATCH" in m]
        assert dispatch_msgs
        parsed = _parse_dispatch_msg(dispatch_msgs[0])
        assert (
            parsed is not None
        ), f"_parse_dispatch_msg could not parse: {dispatch_msgs[0]!r}"
        assert parsed["ticket"] == "T-parse-test"


# ── Stage 3: cc_task_listener drives sprint → in_progress ────────────────────


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not available")
class TestTaskListenerDispatchChain:
    """Integration: GRANNY_DISPATCH in channel → poll_once() → ticket in_progress.

    Inserts a sprint ticket and a GRANNY_DISPATCH message directly into Postgres,
    then calls TaskListener.poll_once() with HWM patched so only our message is
    seen.  The cc_queue.py dispatch subprocess must mark the ticket in_progress.

    Asserts ticket status (primary) — the key invariant that was broken when
    cc_task_listener was not started from GrannyDaemon.
    """

    def _insert_sprint_ticket(self, conn: psycopg2.extensions.connection) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clan.memories (id, memory_type, parent_id, metadata)"
                " VALUES (%s, 'PROCEDURAL', %s, %s)"
                " ON CONFLICT (id) DO UPDATE SET metadata = EXCLUDED.metadata",
                (
                    _TICKET_ID,
                    "TICKETS_ROOT",
                    psycopg2.extras.Json(
                        {
                            "id": _TICKET_ID,
                            "title": "[sprint] Granny dispatch smoke test",
                            "status": "sprint",
                            "worker": "claude",
                            "gate": None,
                            "priority": 0.01,
                            "size": "S",
                            "tags": ["Platform"],
                            "decision_id": None,
                            "description": "temporary integration test ticket",
                            "result": None,
                            "claimed_at": None,
                            "target_difficulty": 1,
                            "kind": "ticket",
                            "test_data": "true",
                        }
                    ),
                ),
            )
        conn.commit()

    def _post_dispatch_msg(
        self, conn: psycopg2.extensions.connection
    ) -> tuple[int, int]:
        """Insert GRANNY_DISPATCH into infra.channel_messages.

        Returns (hwm_before, msg_id) — hwm_before is the max channel message id
        just before insertion, so _read_hwm can be patched to that value and
        poll_once() will only see our new message.
        """
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(id), 0) FROM infra.channel_messages"
                " WHERE channel = 'shared'"
            )
            hwm_before: int = cur.fetchone()[0]

        content = (
            f"GRANNY_DISPATCH|ticket={_TICKET_ID}|worker=claude"
            f"|size=S|tags=Platform|title=smoke+test"
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO infra.channel_messages"
                " (ts, author, type, content, channel)"
                " VALUES (NOW(), 'granny-weatherwax', 'message', %s, 'shared')"
                " RETURNING id",
                (content,),
            )
            msg_id: int = cur.fetchone()[0]
        conn.commit()
        return hwm_before, msg_id

    def _cleanup(
        self,
        conn: psycopg2.extensions.connection,
        msg_id: int | None,
    ) -> None:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM clan.memories WHERE id = %s", (_TICKET_ID,))
                if msg_id is not None:
                    cur.execute(
                        "DELETE FROM infra.channel_messages WHERE id = %s",
                        (msg_id,),
                    )
            conn.commit()
        except Exception:
            pass

    def test_sprint_ticket_reaches_in_progress(self) -> None:
        from lab.claudecode.cc_task_listener import TaskListener

        conn = psycopg2.connect(_DB_URL)
        msg_id: int | None = None
        try:
            self._insert_sprint_ticket(conn)
            hwm_before, msg_id = self._post_dispatch_msg(conn)

            # poll_once() with HWM anchored just before our message.
            # _post_ack is patched to avoid a side-effect write back to the
            # channel — ACK posting is covered separately in test_cc_task_listener.py.
            with (
                patch(
                    "lab.claudecode.cc_task_listener._read_hwm",
                    return_value=hwm_before,
                ),
                patch("lab.claudecode.cc_task_listener._write_hwm"),
                patch("lab.claudecode.cc_task_listener._post_ack"),
            ):
                TaskListener().poll_once()

            # Reopen the connection so we see the status committed by the
            # cc_queue.py dispatch subprocess (the old transaction snapshot
            # would return stale data).
            conn.close()
            conn = psycopg2.connect(_DB_URL)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT metadata->>'status' FROM clan.memories WHERE id = %s",
                    (_TICKET_ID,),
                )
                row = cur.fetchone()

            status = row[0] if row else None
            assert status == "in_progress", (
                f"Expected ticket {_TICKET_ID!r} to be in_progress after poll_once(),"
                f" got {status!r}.  cc_task_listener failed to dispatch."
            )

        finally:
            self._cleanup(conn, msg_id)
            conn.close()
