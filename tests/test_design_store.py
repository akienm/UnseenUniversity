"""Contract tests for the DESIGN artifact — T-design-first-artifact-type.

The design is the first-class artifact that realizes an intention and folds the
former `decision` type in as a fork-resolution (``body.forks[]``). These tests
pin the contract and its non-evadable write gate.

PROOF NODE (proof-on-close, red->green a hollow build can't pass):
    test_forkless_design_rejected
A design that resolved NO fork — or a fork with no ``why`` — is the hollow shape
this artifact exists to reject. Stub (commit A) does not enforce it, so the node
fails with "DID NOT RAISE" (authentic behaviour red); the enforced contract
(commit B) rejects it (green). The other tests hold in both states.
"""

import json
from pathlib import Path

import pytest

from unseen_university import design_store as ds
from devlab.claudecode import design_emit


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    yield tmp_path


def _complete_design(**overrides) -> dict:
    """A minimal design body that satisfies the full contract."""
    body = {
        "design_id": "Design-sample-2026-07-10",
        "title": "A sample design realizing an intention",
        "status": "open",
        "date": "2026-07-10",
        "intentions": ["I intend that the sample subsystem does X observably."],
        "shape": "The subsystem is a store module plus a validated writer.",
        "forks": [
            {
                "question": "One writer that validates, or validation left to callers?",
                "options": ["writer validates", "callers validate"],
                "resolution": "the writer validates before write",
                "why": "a soft caller-side gate is attacker-controlled (hollow slips through)",
            }
        ],
        "proof_obligations": ["forkless design is rejected by validate_design"],
        "spawned_tickets": ["T-sample-a", "T-sample-b"],
        "hypothesis": "Designs stop being decisions; forks carry their why.",
        "measurement_signal": "validate_design rejects a forkless design.",
        "text": "# Design-sample\nnarrative body readers render.",
    }
    body.update(overrides)
    return body


# ── PROOF NODE ─────────────────────────────────────────────────────────────────
def test_forkless_design_rejected():
    """A design that resolved no fork is not a design — it must be rejected."""
    forkless = _complete_design(forks=[])
    with pytest.raises(ds.DesignValidationError):
        ds.validate_design(forkless)


def test_fork_missing_why_rejected():
    """A fork without its ``why`` violates CP3 (every artifact carries its why)."""
    no_why = _complete_design(forks=[{
        "question": "A or B?",
        "resolution": "A",
        # no "why"
    }])
    with pytest.raises(ds.DesignValidationError):
        ds.validate_design(no_why)


def test_complete_design_accepted():
    """The full, honest design passes the contract."""
    ds.validate_design(_complete_design())  # must not raise


def test_missing_intention_rejected():
    """A design that realizes no intention is rejected (front edge of the stack)."""
    with pytest.raises(ds.DesignValidationError):
        ds.validate_design(_complete_design(intentions=[]))


def test_emit_validates_before_write():
    """The write gate is enforced in code: a hollow design never reaches the store."""
    with pytest.raises(ds.DesignValidationError):
        design_emit.emit_design(_complete_design(forks=[]))
    # Nothing was written.
    assert ds.get_design("Design-sample-2026-07-10") is None


def test_design_roundtrips_fork_with_why():
    """emit -> read back: the fork decision AND its why are retrievable (not just
    an envelope). Envelope != work."""
    out = design_emit.emit_design(_complete_design(), project_decision=False)
    assert Path(out["design_path"]).exists()

    rec = ds.get_design("Design-sample-2026-07-10")
    assert rec is not None
    fork = rec["body"]["forks"][0]
    assert fork["resolution"] == "the writer validates before write"
    assert "attacker-controlled" in fork["why"]


def test_projection_lands_backcompat_decision():
    """emit projects a derived decision so the legacy decisions/ readers keep
    working (design is the source of truth; the D-* is a read-model)."""
    out = design_emit.emit_design(_complete_design())
    dec_path = Path(out["decision_path"])
    assert dec_path.exists()
    body = json.loads(dec_path.read_text())["body"]
    assert body["decision_id"] == "D-sample-2026-07-10"
    assert body["spawned_tickets"] == ["T-sample-a", "T-sample-b"]
    assert body["projected_from_design"] == "Design-sample-2026-07-10"
