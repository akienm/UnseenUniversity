"""
Proof for the architect file-mention → inject handshake (T-aider-port-file-mention-handshake).

The architect spends turns Reading files it could just name (F-A/F-D). aider's architect NAMES
repo files and the harness feeds their content back. The discriminator (the ticket's own test):
a NAMED REAL repo file is resolved/injected; a named NON-EXISTENT file is not — deterministic
word/basename matching, no fuzzy identity.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from unseen_university.devices.inference.domains.architect_editor import (
    ArchitectEditorFlow,
    _repo_relative_files,
    get_file_mentions,
)


# ── THE PROOF NODE — real file resolves, non-existent does not ────────────────

def test_get_file_mentions_matches_real_not_ghost(tmp_path):
    (tmp_path / "engine.py").write_text("def go():\n    return 1\n")
    repo_files = _repo_relative_files(tmp_path)
    mentions = get_file_mentions(
        "Please edit engine.py to fix it — but the missing ghost.py file is not here.", repo_files
    )
    assert "engine.py" in mentions, "a named REAL repo file must be resolved"
    assert "ghost.py" not in mentions, "a named NON-EXISTENT file must not be resolved"


# ── Handshake e2e (via the flow with a fake inference device) ─────────────────

def _resp(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.input_tokens = r.output_tokens = 10
    r.cost_estimate = 0.0
    return r


class _FakeDev:
    """Architect (tools offered) → a plan naming `plan_text`; block editor (tools None) → empty."""

    def __init__(self, plan_text):
        self._plan = plan_text
        self.architect_msgs = []

    def dispatch(self, req):
        if req.tools is None:  # block editor phase — irrelevant to these assertions
            return _resp("")
        self.architect_msgs.append(
            " ".join(m.get("content", "") for m in req.messages if isinstance(m.get("content"), str))
        )
        return _resp(json.dumps({"status": "done", "result": self._plan,
                                 "error_class": None, "error_number": None}))


def _run(tmp_path, dev, ticket_id="T-fm"):
    ArchitectEditorFlow(block_editor_enabled=True, inference_device=dev).run(
        system_prompt="", initial_message="make the change", ticket_id=ticket_id, cwd=tmp_path,
    )


def test_named_file_triggers_one_reflection_with_content_injected(tmp_path):
    """Naming a real repo file injects its full content and reflects the architect exactly once."""
    (tmp_path / "target.py").write_text("SECRET_MARKER = 42\n")
    dev = _FakeDev("Edit target.py to change the marker.")
    _run(tmp_path, dev)
    assert len(dev.architect_msgs) == 2, (
        f"exactly one reflection expected (2 architect passes), got {len(dev.architect_msgs)}"
    )
    assert "SECRET_MARKER = 42" in dev.architect_msgs[1], (
        "the named file's content must be injected into the reflection pass"
    )


def test_nonexistent_file_does_not_inject(tmp_path):
    """Naming only a non-existent file causes NO reflection — nothing to inject."""
    (tmp_path / "real.py").write_text("x = 1\n")
    dev = _FakeDev("We should edit ghost.py, which does not exist in the repo.")
    _run(tmp_path, dev)
    assert len(dev.architect_msgs) == 1, (
        f"no reflection when only a non-existent file is named, got {len(dev.architect_msgs)}"
    )


def test_resolved_file_set_emitted_to_corpus(tmp_path, monkeypatch):
    """The architect's resolved file-set is emitted to the corpus (a nexus-row candidate)."""
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path / "corpus"))
    (tmp_path / "target.py").write_text("y = 2\n")
    _run(tmp_path, _FakeDev("Edit target.py now."), ticket_id="T-fm-corpus")

    records = []
    for jf in (tmp_path / "corpus").rglob("*.jsonl"):
        for line in jf.read_text(encoding="utf-8").splitlines():
            records.append(json.loads(line))
    file_sets = [r for r in records if r.get("kind") == "architect_file_set"]
    assert file_sets and "target.py" in file_sets[0]["files"], (
        f"resolved file-set must be emitted to corpus; records: {records}"
    )
