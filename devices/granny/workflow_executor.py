"""
workflow_executor.py — Poll-cycle workflow state machine for Granny.

Workflow scripts declare a sequence of dispatch steps with optional "after"
dependencies. Granny calls WorkflowExecutor.tick() on each poll cycle to
advance all active workflows.

External state principle: every workflow's progress is persisted in
~/.granny/workflows/{workflow_id}.json so Granny can restart freely.

Workflow script format (Python module):
    WORKFLOW_ID = "my-workflow"
    STEPS = [
        {"id": "step-1", "dispatch": "DickSimnel.0", "ticket": "T-foo"},
        {"id": "step-2", "dispatch": "DickSimnel.0", "ticket": "T-bar", "after": ["step-1"]},
    ]

Step status lifecycle: pending → dispatched → done | failed
Workflow status lifecycle: pending → running → completed | failed
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_GRANNY_HOME = Path.home() / ".granny"
_WORKFLOWS_DIR = _GRANNY_HOME / "workflows"
_UU_ROOT = Path(__file__).resolve().parents[2]
_CC_QUEUE = _UU_ROOT / "devlab" / "claudecode" / "cc_queue.py"
_PYTHON = sys.executable
_DB_URL = os.environ.get(
    "UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)

# Terminal ticket statuses that mean "the step is done"
_DONE_STATUSES = {"closed", "done"}
# Terminal statuses that mean "the step failed / needs intervention"
_FAILED_STATUSES = {"escalated", "hold", "cancelled"}


# ── Workflow script loading ───────────────────────────────────────────────────


def load_workflow_script(script_path: str | Path) -> dict:
    """Load a workflow script module and return its WORKFLOW_ID and STEPS."""
    path = Path(script_path)
    spec = importlib.util.spec_from_file_location("_wf_script", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load workflow script: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {
        "workflow_id": mod.WORKFLOW_ID,
        "steps": list(mod.STEPS),
    }


# ── State persistence ─────────────────────────────────────────────────────────


def _state_path(workflow_id: str) -> Path:
    return _WORKFLOWS_DIR / f"{workflow_id}.json"


def load_state(workflow_id: str) -> dict | None:
    p = _state_path(workflow_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        log.warning("Workflow: failed to load state for %s: %s", workflow_id, exc)
        return None


def save_state(state: dict) -> None:
    _WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    p = _state_path(state["workflow_id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.rename(p)
    log.debug("Workflow: saved state for %s (status=%s)", state["workflow_id"], state["status"])


def list_active_workflows() -> list[dict]:
    """Return all workflow state dicts that are not completed or failed."""
    if not _WORKFLOWS_DIR.exists():
        return []
    active = []
    for p in _WORKFLOWS_DIR.glob("*.json"):
        try:
            state = json.loads(p.read_text())
            if state.get("status") not in ("completed", "failed"):
                active.append(state)
        except Exception as exc:
            log.warning("Workflow: skipping unreadable state file %s: %s", p.name, exc)
    return active


def start_workflow(script_path: str | Path) -> dict:
    """Create initial state for a workflow and persist it. Returns the state dict."""
    script = load_workflow_script(script_path)
    wid = script["workflow_id"]
    steps_state = {
        s["id"]: {
            "status": "pending",
            "ticket": s["ticket"],
            "dispatch": s.get("dispatch", "DickSimnel.0"),
            "after": s.get("after", []),
        }
        for s in script["steps"]
    }
    state = {
        "workflow_id": wid,
        "script_path": str(script_path),
        "status": "pending",
        "steps": steps_state,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_state(state)
    log.info("Workflow: started %s (%d steps)", wid, len(steps_state))
    return state


# ── Ticket status query ───────────────────────────────────────────────────────


def get_ticket_status(ticket_id: str) -> str | None:
    """Return the current status of a ticket, or None on error."""
    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata->>'status' FROM clan.memories "
                "WHERE metadata->>'id' = %s AND metadata->>'kind' = 'ticket' LIMIT 1",
                (ticket_id,),
            )
            row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as exc:
        log.debug("Workflow: ticket status query failed for %s: %s", ticket_id, exc)
        return None


# ── Dispatch ──────────────────────────────────────────────────────────────────


def _dispatch_step(step_id: str, ticket_id: str, worker: str, workers_cfg: dict) -> bool:
    """Dispatch a workflow step's ticket to the named worker via cc_queue.py set-worker."""
    wcfg = workers_cfg.get(worker, {})
    worker_name = wcfg.get("worker_name", worker.split(".")[0].lower())
    r = subprocess.run(
        [_PYTHON, str(_CC_QUEUE), "set-worker", worker_name, ticket_id],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "UU_HOME_DB_URL": _DB_URL},
    )
    if r.returncode != 0:
        log.warning("Workflow: set-worker %s failed for %s/%s: %s", worker_name, step_id, ticket_id, r.stderr[:100])
        return False
    log.info(
        "Workflow: dispatched step %s ticket %s → %s (set-worker %s)",
        step_id, ticket_id, worker, worker_name,
    )
    return True


# ── State machine tick ────────────────────────────────────────────────────────


def tick_workflow(state: dict, workers_cfg: dict) -> dict:
    """Advance one workflow by one poll cycle. Returns updated state (not yet saved)."""
    wid = state["workflow_id"]
    steps = state["steps"]

    if state["status"] == "pending":
        state["status"] = "running"
        log.info("Workflow: %s → running", wid)

    any_change = False

    for step_id, step in steps.items():
        step_status = step["status"]

        if step_status in ("done", "failed"):
            continue

        if step_status == "pending":
            # Gate check: all "after" dependencies must be "done"
            deps = step.get("after", [])
            if all(steps[d]["status"] == "done" for d in deps if d in steps):
                # Find which workflow step spec this is
                ticket_id = step["ticket"]
                worker = step.get("dispatch", "DickSimnel.0")
                ok = _dispatch_step(step_id, ticket_id, worker, workers_cfg)
                if ok:
                    step["status"] = "dispatched"
                    any_change = True
                    log.info(
                        "Workflow %s: step %s → dispatched (ticket=%s, gate passed)",
                        wid, step_id, ticket_id,
                    )
                else:
                    log.warning("Workflow %s: step %s dispatch failed — will retry next cycle", wid, step_id)

        elif step_status == "dispatched":
            ticket_id = step["ticket"]
            tstat = get_ticket_status(ticket_id)
            if tstat in _DONE_STATUSES:
                step["status"] = "done"
                any_change = True
                log.info(
                    "Workflow %s: step %s → done (ticket=%s status=%s)",
                    wid, step_id, ticket_id, tstat,
                )
            elif tstat in _FAILED_STATUSES:
                step["status"] = "failed"
                state["status"] = "failed"
                any_change = True
                log.warning(
                    "Workflow %s: step %s → failed (ticket=%s status=%s) — workflow halted",
                    wid, step_id, ticket_id, tstat,
                )
                break

    # Check completion
    if state["status"] == "running":
        if all(s["status"] == "done" for s in steps.values()):
            state["status"] = "completed"
            log.info("Workflow %s: all %d steps done → completed", wid, len(steps))
            any_change = True
        elif any(s["status"] == "failed" for s in steps.values()):
            state["status"] = "failed"

    if any_change:
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    return state


class WorkflowExecutor:
    """Tick all active workflows on each Granny poll cycle."""

    def tick(self, workers_cfg: dict) -> int:
        """Advance all active workflows. Returns the number of workflows ticked."""
        active = list_active_workflows()
        if not active:
            return 0

        count = 0
        for state in active:
            wid = state.get("workflow_id", "?")
            try:
                updated = tick_workflow(state, workers_cfg)
                save_state(updated)
                count += 1
            except Exception as exc:
                log.warning("Workflow %s: tick failed: %s", wid, exc)

        if count:
            log.debug("Workflow: ticked %d active workflow(s)", count)
        return count


_executor = WorkflowExecutor()


def get_executor() -> WorkflowExecutor:
    return _executor
