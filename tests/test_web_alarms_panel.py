"""Proof for T-system-alarms-web-panel.

The /api/alarms endpoint reflects the flat-file alarm store with human-rendered
fields (datetime/emitter/description + a detail block), so the ALARMS PANEL has
data to render. Calls the endpoint function directly to avoid booting the whole
web app.
"""

from __future__ import annotations

import json

import pytest

from unseen_university import system_alarms as sa


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    monkeypatch.setattr("unseen_university.system_alarms.uu_home", lambda: str(tmp_path))
    return tmp_path


async def _call_alarms():
    from devices.web_server.server import _api_alarms

    resp = await _api_alarms(request=None)
    return json.loads(bytes(resp.body).decode("utf-8"))


def test_empty_when_no_alarms():
    import asyncio

    data = asyncio.run(_call_alarms())
    assert data == {"alarms": []}


def test_reflects_dropped_alarm_with_human_fields():
    sa.raise_alarm("no-provider:worker", "devices.inference.device", "no source", emit_log=False)
    sa.raise_alarm("no-provider:worker", "devices.inference.device", "no source", emit_log=False)
    sa.raise_alarm("no-provider:worker", "devices.igor.cognition", "no source", emit_log=False)

    import asyncio

    data = asyncio.run(_call_alarms())
    assert len(data["alarms"]) == 1
    a = data["alarms"][0]
    # human-rendered list fields — no raw JSON / no dict internals leaked
    assert a["emitter"] == "devices.inference.device"  # primary caller (max count)
    assert a["description"] == "no source"
    assert a["datetime"]  # populated, human format (no 'T')
    assert "T" not in a["datetime"]
    # detail pane fields are rendered strings, not nested structures
    d = a["detail"]
    assert d["signature"] == "no-provider:worker"
    assert "3×" in d["seen"]
    assert "devices.inference.device×2" in d["callers"]


def test_panel_is_injected_on_all_pages_and_hidden_by_default():
    """The ALARMS PANEL ships in the shared _html_wrap shell and starts hidden."""
    from devices.web_server.server import _html_wrap, _ALARMS_PANEL

    assert "SYSTEM ALARMS" in _ALARMS_PANEL
    assert "display:none" in _ALARMS_PANEL  # conditional — hidden until alarms exist
    page = _html_wrap("Any Page", "<p>body</p>")
    assert 'id="sysalarm-panel"' in page  # present on every _html_wrap page
