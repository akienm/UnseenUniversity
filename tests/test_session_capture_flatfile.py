"""Proof test for T-fix-session-capture-flatfile.

Behavioral claim: session_capture.capture() writes the session record as a
flat-file node in the canonical store (devlab/runtime/memory/sessions/) via
memory_emit — with the summary + text-only transcript — and makes NO Postgres
connection (the dead adc.palace write path is gone).

A hollow implementation (errors out, writes nothing, or writes to the wrong
place / without the content) fails the value assertions below — authentic red.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "devlab" / "claudecode"))

import memory_emit  # noqa: E402
import session_capture  # noqa: E402


def _make_session_jsonl(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                json.dumps({"message": {"role": "user", "content": "build the thing"}}),
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "secret reasoning"},
                                {"type": "text", "text": "done — shipped the thing"},
                            ],
                        }
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_session_capture_writes_flatfile_node_no_psql(tmp_path, monkeypatch):
    # Redirect the memory store to a temp dir (emit reads MEMORY_ROOT at call time).
    monkeypatch.setattr(memory_emit, "MEMORY_ROOT", str(tmp_path / "memory"))

    sess = _make_session_jsonl(tmp_path / "sess.jsonl")
    result = session_capture.capture(sess, summary_text="proof summary line")

    # A node was written under the canonical sessions/ store dir.
    node = Path(result["session_node"])
    assert node.exists(), f"no session node written: {result}"
    assert node.parent == tmp_path / "memory" / "sessions", (
        f"session node not in canonical sessions/ store: {node}"
    )

    data = json.loads(node.read_text(encoding="utf-8"))
    body = data["body"]
    assert data["category"] == "sessions"
    assert body["summary"] == "proof summary line"
    # Transcript carries the text turns, strips thinking/tool blocks.
    assert "done — shipped the thing" in body["transcript"]
    assert "secret reasoning" not in body["transcript"]
    assert result["turns"] == 2

    # No Postgres: the dead adc.palace / psycopg2 write path is gone.
    src = (_REPO / "scripts" / "session_capture.py").read_text(encoding="utf-8")
    assert "adc.palace" not in src and "psycopg2" not in src, (
        "dead adc.palace / psycopg2 path still present in session_capture.py"
    )
