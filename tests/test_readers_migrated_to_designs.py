"""Proof — decisions/ readers migrated to designs/ (T-migrate-decision-readers-to-designs).

The design is the single home; the decision projection is retired as a write and
becomes a READ-model (``design_store.iter_decision_view``). These tests pin that a
projection-LESS design (emitted with the new default, no materialised ``D-*``) is
still surfaced by the readers — and the dedup that stops a transition-era
materialised projection from double-surfacing.

PROOF NODE (proof-on-close, red->green a hollow build can't pass):
    test_context_load_recent_decisions_surfaces_projectionless_design
Runs the ACTUAL ``skills/context-load/run`` reader end-to-end against a store that
holds ONLY a design (no decisions/ file). Pre-migration, ``_decisions_newest_first``
globbed ``decisions/`` — the design was invisible, so Step 2a would NOT print its
id (red). Post-migration the helper reads ``iter_decision_view``, which projects the
design on read, so its derived ``D-*`` id appears (green). A hollow build that left
the reader globbing decisions/ fails this node.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from unseen_university import design_store as ds
from unseen_university._uu_root import uu_root

_REPO = Path(uu_root())


def _design_envelope(design_id="Design-migrated-2026-07-10", **body_over) -> dict:
    body = {
        "design_id": design_id,
        "title": "A design that never materialised a decision",
        "status": "open",
        "date": "2026-07-10",
        "intentions": ["I intend the readers point at designs."],
        "shape": "the read-model projects on read",
        "forks": [{"question": "q", "resolution": "r", "why": "w"}],
        "proof_obligations": [],
        "spawned_tickets": ["T-a"],
        "text": "narrative the readers render",
    }
    body.update(body_over)
    return {
        "id": f"cc.0.{design_id}",
        "emitter": "cc.0",
        "namespace": [design_id],
        "kind": "design",
        "emitted_at": "2026-07-10T12:00:00",
        "body": body,
    }


def _write(root: Path, category: str, name: str, envelope: dict) -> None:
    d = root / category
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(envelope), encoding="utf-8")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    return tmp_path


# ── read-model unit proofs ──────────────────────────────────────────────────────
def test_view_projects_projectionless_design_envelope_shaped(store):
    """A design with NO materialised decision surfaces in the view as an
    envelope-shaped decision record (top-level namespace/emitted_at, body.status)."""
    _write(store, "designs", "d.json", _design_envelope())
    view = list(ds.iter_decision_view())
    assert len(view) == 1
    rec = view[0]
    # envelope framing the readers key on (advisor trap #1)
    assert rec["namespace"] == ["D-migrated-2026-07-10"]
    assert rec["emitted_at"] == "2026-07-10T12:00:00"
    assert rec["body"]["status"] == "open"
    assert rec["body"]["decision_id"] == "D-migrated-2026-07-10"
    assert rec["body"]["projected_from_design"] == "Design-migrated-2026-07-10"


def test_view_dedups_materialised_projection(store):
    """When a design AND a transition-era materialised projection of it both exist,
    the view yields the decision id exactly once — via the live design (trap #2)."""
    _write(store, "designs", "d.json", _design_envelope())
    # a stale materialised projection (what the old default wrote)
    proj = {
        "id": "cc.0.D-migrated", "emitter": "cc.0",
        "namespace": ["D-migrated-2026-07-10"], "kind": "decision",
        "emitted_at": "2026-07-10T11:00:00",
        "body": ds.project_decision_body(_design_envelope()["body"]),
    }
    _write(store, "decisions", "proj.json", proj)
    ids = [r["namespace"][0] for r in ds.iter_decision_view()]
    assert ids.count("D-migrated-2026-07-10") == 1


def test_view_keeps_historical_independent_decisions(store):
    """A genuine historical decision (not a projection) still surfaces — decisions/
    stays readable (scope boundary)."""
    hist = {
        "id": "cc.0.D-old", "emitter": "cc.0", "namespace": ["D-old-2026-06-01"],
        "kind": "decision", "emitted_at": "2026-06-01T09:00:00",
        "body": {"decision_id": "D-old-2026-06-01", "title": "legacy", "status": "open"},
    }
    _write(store, "decisions", "old.json", hist)
    _write(store, "designs", "d.json", _design_envelope())
    ids = {r["namespace"][0] for r in ds.iter_decision_view()}
    assert ids == {"D-old-2026-06-01", "D-migrated-2026-07-10"}


def test_outcome_resolves_D_id_to_design_file(store):
    """The /outcome read/write path is handed a decision-shaped ``D-<slug>`` (what
    the list surfaces) but the design file is named ``Design-<slug>`` — a naive
    ``*D-<slug>*`` glob would miss it. Pin the inverse-map resolution the skill uses
    so a design-first outcome review can actually find its record (advisor gap #1)."""
    _write(store, "designs", "d.json",
           _design_envelope(text="body\n## Hypothesis\nthe readers point at designs"))
    did = "D-migrated-2026-07-10"  # what /outcome is invoked with
    # naive substring glob (the bug) finds nothing:
    import glob as _glob
    assert _glob.glob(str(store / "designs" / f"*{did}*.json")) == []
    # inverse-map resolution (the fix) finds the design:
    design_id = "Design-" + did[2:]
    rec = ds.get_design(design_id)
    assert rec is not None
    assert "## Hypothesis" in rec["body"]["text"]


# ── PROOF NODE: the real reader, end-to-end ─────────────────────────────────────
def test_context_load_recent_decisions_surfaces_projectionless_design(store):
    """Run the ACTUAL context-load reader against a store with only a design.
    Its derived D-* id must appear in Step 2a — proving the reader reads the
    design view, not a decisions/ glob (which would find nothing here)."""
    _write(store, "designs", "d.json", _design_envelope())

    env = dict(os.environ)
    env["UU_MEMORY_ROOT"] = str(store)
    env["UU_ROOT"] = str(_REPO)
    proc = subprocess.run(
        [sys.executable, str(_REPO / "skills" / "context-load" / "run")],
        capture_output=True, text=True, env=env, cwd=str(_REPO), timeout=60,
    )
    out = proc.stdout
    # Step 2a / Step 3 render the derived decision id for a design with no
    # materialised D-* — only possible because the reader reads iter_decision_view.
    assert "D-migrated-2026-07-10" in out, (
        f"context-load did not surface the projection-less design.\n"
        f"stdout:\n{out}\nstderr:\n{proc.stderr}"
    )
