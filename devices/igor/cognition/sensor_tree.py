"""
sensor_tree.py — GH-281: SensorTree — generalized monitoring as traversable memory subtree.

Each sensor is a FACTUAL memory node in the graph with metadata:
    watch_type: file_mtime | disk_usage | process_alive | http_endpoint | custom
    target: path, process name, URL, etc.
    condition: changed | threshold_exceeded | unreachable | custom
    threshold: numeric threshold (for threshold_exceeded)
    action_habit_id: habit ID to fire when condition met (optional)
    check_interval_sec: how often to check (default 300)

The SensorTreeSource push source walks sensor nodes, evaluates each
condition, and pushes observations to TWM when conditions trigger.

New sensors = new memory nodes. No Python needed. Igor can create his
own sensors by depositing FACTUAL nodes with the right metadata.

Parent node: SENSOR_TREE_ROOT (under CP3 knowledge)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from .push_sources import BasePushSource
from ..igor_base import get_logger

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = get_logger(__name__)

SENSOR_TREE_ROOT = "SENSOR_TREE_ROOT"
DEFAULT_CHECK_INTERVAL = 300  # 5 minutes


# ── Evaluators ───────────────────────────────────────────────────────────────


def _eval_file_mtime(target: str, meta: dict) -> Optional[dict]:
    """Check if file modification time changed since last check."""
    try:
        stat = os.stat(os.path.expanduser(target))
        current_mtime = stat.st_mtime
        last_mtime = meta.get("_last_mtime")

        if last_mtime is not None and current_mtime != last_mtime:
            return {
                "triggered": True,
                "detail": f"mtime changed: {last_mtime} → {current_mtime}",
                "current_mtime": current_mtime,
            }
        return {"triggered": False, "current_mtime": current_mtime}
    except FileNotFoundError:
        return {"triggered": True, "detail": f"file not found: {target}"}
    except Exception as exc:
        return {"triggered": False, "error": str(exc)}


def _eval_disk_usage(target: str, meta: dict) -> Optional[dict]:
    """Check disk usage against threshold."""
    import shutil

    try:
        usage = shutil.disk_usage(os.path.expanduser(target))
        pct = (usage.used / usage.total) * 100
        threshold = meta.get("threshold", 90)

        return {
            "triggered": pct >= threshold,
            "detail": f"disk {pct:.1f}% used (threshold {threshold}%)",
            "current_value": round(pct, 1),
        }
    except Exception as exc:
        return {"triggered": False, "error": str(exc)}


def _eval_process_alive(target: str, meta: dict) -> Optional[dict]:
    """Check if a named process is running."""
    import subprocess

    try:
        result = subprocess.run(
            ["pgrep", "-f", target],
            capture_output=True,
            timeout=5,
        )
        alive = result.returncode == 0
        condition = meta.get("condition", "unreachable")

        if condition == "unreachable":
            return {
                "triggered": not alive,
                "detail": f"process '{target}' {'alive' if alive else 'not found'}",
            }
        return {"triggered": alive, "detail": f"process '{target}' is alive"}
    except Exception as exc:
        return {"triggered": False, "error": str(exc)}


def _eval_http_endpoint(target: str, meta: dict) -> Optional[dict]:
    """Check if an HTTP endpoint is reachable."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(target, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
        threshold = meta.get("threshold", 400)
        return {
            "triggered": status >= threshold,
            "detail": f"HTTP {status} from {target}",
            "status_code": status,
        }
    except urllib.error.URLError as exc:
        return {"triggered": True, "detail": f"unreachable: {target} ({exc.reason})"}
    except Exception as exc:
        return {"triggered": False, "error": str(exc)}


# Evaluator dispatch table
_EVALUATORS = {
    "file_mtime": _eval_file_mtime,
    "disk_usage": _eval_disk_usage,
    "process_alive": _eval_process_alive,
    "http_endpoint": _eval_http_endpoint,
}


def evaluate_sensor(sensor_meta: dict) -> Optional[dict]:
    """
    Evaluate a single sensor node's condition.

    Returns dict with at least 'triggered' (bool) key, or None on error.
    """
    watch_type = sensor_meta.get("watch_type", "")
    target = sensor_meta.get("target", "")

    if not watch_type or not target:
        return None

    evaluator = _EVALUATORS.get(watch_type)
    if evaluator is None:
        return None

    return evaluator(target, sensor_meta)


# ── Push Source ───────────────────────────────────────────────────────────────


class SensorTreeSource(BasePushSource):
    """
    Push source that walks the sensor tree and evaluates each sensor.

    Replaces the need for Python-coded monitors — new sensors are just
    memory nodes with the right metadata. Igor can create his own sensors.
    """

    name = "sensor_tree"
    TIMING_TIER = "slow"  # 300s interval
    CHECK_INTERVAL_SEC = 60  # check tree every minute, per-sensor intervals vary

    def __init__(self):
        self._last_check: Optional[float] = None
        self._sensor_state: dict = {}  # sensor_id → {last_check, last_mtime, ...}
        self._suppressed: set = set()  # sensor IDs that triggered and haven't cleared

    def push(self, cortex: "Cortex") -> list[int]:
        now = time.monotonic()
        if (
            self._last_check is not None
            and now - self._last_check < self.CHECK_INTERVAL_SEC
        ):
            return []
        self._last_check = now

        sensors = self._load_sensors(cortex)
        if not sensors:
            return []

        pushed = []
        for sensor in sensors:
            sensor_id = sensor.get("id", "")
            meta = sensor.get("metadata", {})
            interval = meta.get("check_interval_sec", DEFAULT_CHECK_INTERVAL)

            # Per-sensor interval check
            state = self._sensor_state.get(sensor_id, {})
            last_check = state.get("last_check_mono")
            if last_check is not None and now - last_check < interval:
                continue

            # Carry forward state (like last_mtime)
            eval_meta = dict(meta)
            eval_meta.update(state)

            result = evaluate_sensor(eval_meta)
            if result is None:
                continue

            # Update state
            new_state = {"last_check_mono": now}
            if "current_mtime" in result:
                new_state["_last_mtime"] = result["current_mtime"]
            self._sensor_state[sensor_id] = new_state

            if not result.get("triggered"):
                # Condition cleared — remove suppression
                self._suppressed.discard(sensor_id)
                continue

            # Suppress repeated triggers
            if sensor_id in self._suppressed:
                continue
            self._suppressed.add(sensor_id)

            # Wire palace_metric: record sensor alert to history
            try:
                from ..tools.palace_metric import append_history

                history_path = f"unseenuniversity/metrics/sensors/{sensor_id}"
                with cortex._db() as conn:
                    append_history(
                        conn,
                        history_path,
                        f"{watch_type}:1",
                        actor="sensor_tree",
                    )
            except Exception as e:
                # Non-fatal: palace_metric recording failure doesn't block sensor alert
                logger.debug("SensorTreeSource: palace_metric.record failed: %s", e)

            # Push to TWM
            detail = result.get("detail", "triggered")
            watch_type = meta.get("watch_type", "unknown")
            target = meta.get("target", "")
            salience = meta.get("sensor_salience", 0.65)
            urgency = meta.get("sensor_urgency", 0.5)
            ttl = meta.get("sensor_ttl_sec", 300)

            csb = (
                f"SENSOR_ALERT|{sensor_id}|{watch_type}"
                f"|target={target}|{detail[:200]}"
            )
            obs_id = cortex.twm_push(
                source=self.name,
                content_csb=csb,
                salience=salience,
                urgency=urgency,
                ttl_seconds=ttl,
                metadata={
                    "sensor_id": sensor_id,
                    "watch_type": watch_type,
                    "target": target,
                    "detail": detail[:500],
                },
            )
            pushed.append(obs_id)

            # Fire associated habit if configured
            action_habit_id = meta.get("action_habit_id")
            if action_habit_id:
                try:
                    cortex.twm_push(
                        source="sensor_tree",
                        content_csb=(
                            f"ACTION_IMPULSE|SENSOR_HABIT|id={action_habit_id}"
                            f"|sensor={sensor_id}|{detail[:100]}"
                        ),
                        salience=0.7,
                        urgency=0.6,
                        ttl_seconds=600,
                        metadata={
                            "habit_id": action_habit_id,
                            "sensor_id": sensor_id,
                        },
                    )
                except Exception as e:
                    logger.debug("SensorTreeSource: cortex.twm_write failed: %s", e)

        return pushed

    def _load_sensors(self, cortex: "Cortex") -> list[dict]:
        """Load sensor nodes from the graph (children of SENSOR_TREE_ROOT)."""
        try:
            mem = cortex.get(SENSOR_TREE_ROOT)
            if mem is None:
                return []

            # Get children of the root
            children = cortex.get_children(SENSOR_TREE_ROOT)
            sensors = []
            for child in children:
                meta = child.metadata or {}
                if meta.get("watch_type"):
                    sensors.append(
                        {
                            "id": child.id,
                            "narrative": child.narrative,
                            "metadata": meta,
                        }
                    )
            return sensors
        except Exception as exc:
            logger.debug("SensorTree load failed: %s", exc)
            return []


def ensure_sensor_root(cortex: "Cortex") -> None:
    """Create the SENSOR_TREE_ROOT node if it doesn't exist."""
    try:
        existing = cortex.get(SENSOR_TREE_ROOT)
        if existing:
            return

        from ..memory.models import Memory, MemoryType

        root = Memory(
            id=SENSOR_TREE_ROOT,
            narrative="Root node for Igor's sensor tree — generalized monitoring as memory nodes.",
            memory_type=MemoryType.FACTUAL,
            parent_id="CP3",
            source="sensor_tree",
            certainty=1.0,
            metadata={"spine": True, "sensor_tree_root": True},
        )
        cortex.store(root)
        logger.info("Created SENSOR_TREE_ROOT node")
    except Exception as exc:
        logger.warning("Failed to create SENSOR_TREE_ROOT: %s", exc)


def create_sensor(
    cortex: "Cortex",
    sensor_id: str,
    narrative: str,
    watch_type: str,
    target: str,
    condition: str = "changed",
    threshold: float = None,
    action_habit_id: str = None,
    check_interval_sec: int = DEFAULT_CHECK_INTERVAL,
) -> dict:
    """
    Create a new sensor node in the sensor tree.

    This is how Igor adds new sensors — no Python needed.
    """
    ensure_sensor_root(cortex)

    from ..memory.models import Memory, MemoryType

    meta = {
        "watch_type": watch_type,
        "target": target,
        "condition": condition,
        "check_interval_sec": check_interval_sec,
        "deposited_by": "sensor_tree",
    }
    if threshold is not None:
        meta["threshold"] = threshold
    if action_habit_id:
        meta["action_habit_id"] = action_habit_id

    sensor = Memory(
        id=sensor_id,
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
        parent_id=SENSOR_TREE_ROOT,
        source="sensor_tree",
        certainty=1.0,
        metadata=meta,
    )
    cortex.store(sensor)
    return {"sensor_id": sensor_id, "watch_type": watch_type, "target": target}
