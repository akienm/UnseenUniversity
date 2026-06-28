"""Proof for T-system-alarms-panel-coverage.

The ALARMS PANEL must reach the pages that bypass _html_wrap: the chat page
(served by _index), the dashboard, and the metrics page. _inject_alarms_panel
inserts the single _ALARMS_PANEL source right after <body>.
"""

from __future__ import annotations

import asyncio


def test_inject_inserts_panel_after_body():
    from unseen_university.devices.web_server.server import _inject_alarms_panel

    out = _inject_alarms_panel("<html><head></head><body>hello</body></html>")
    assert 'id="sysalarm-panel"' in out
    # injected AFTER <body>, before the page's own content
    assert out.index("<body>") < out.index("sysalarm-panel") < out.index("hello")


def test_inject_is_idempotent_and_safe_without_body():
    from unseen_university.devices.web_server.server import _inject_alarms_panel, _ALARMS_PANEL

    once = _inject_alarms_panel("<body>x</body>")
    twice = _inject_alarms_panel(once)
    assert once == twice  # not double-injected
    assert _inject_alarms_panel("no body here") == "no body here"  # graceful no-op


def _body(resp):
    return bytes(resp.body).decode("utf-8")


def test_chat_dashboard_metrics_pages_carry_the_panel():
    from unseen_university.devices.web_server import server

    chat = asyncio.run(server._index(request=None))
    dash = asyncio.run(server._page_dashboard(request=None))
    metrics = asyncio.run(server._page_metrics(request=None))

    for resp in (chat, dash, metrics):
        assert 'id="sysalarm-panel"' in _body(resp)
        assert "SYSTEM ALARMS" in _body(resp)
