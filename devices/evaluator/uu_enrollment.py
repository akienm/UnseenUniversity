"""
UU self-enrollment — observe→learn→improve loop wired to UU's own device tree.

R-uu-core rubric criteria (LLM-judged from device file content):
  1. inherits_base_device: class inherits BaseDevice
  2. no_sqlite: no 'import sqlite3' in code
  3. no_bare_except: no bare 'except:' clause
  4. has_smoke_test: test file exists alongside the device

run_uu_enrollment() is single-shot — call it once per cycle. No background
thread. Callers schedule their own cadence.

seed_uu_core_rubric() is idempotent — safe on every startup.

D-agentic-os-platform-2026-05-30
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_UU_ROOT = Path(__file__).resolve().parents[2]
_DEVICES_ROOT = _UU_ROOT / "devices"

UU_CORE_RUBRIC_ID = "R-uu-core"

_UU_CORE_CRITERIA = [
    {
        "name": "inherits_base_device",
        "instruction": (
            "The code must import BaseDevice from unseen_university.device and "
            "define a class that inherits from it (e.g. 'class Foo(BaseDevice)'). "
            "Pass if found, fail if not."
        ),
    },
    {
        "name": "no_sqlite",
        "instruction": (
            "The code must NOT contain 'import sqlite3' or 'sqlite3.connect'. "
            "Pass if neither is found, fail if either is present."
        ),
    },
    {
        "name": "no_bare_except",
        "instruction": (
            "The code must NOT contain bare 'except:' clauses (except with no exception "
            "type). 'except Exception:' is acceptable. "
            "Pass if no bare except: is found, fail if any are present."
        ),
    },
    {
        "name": "has_smoke_test",
        "instruction": (
            "A test file must exist for this device. The output header line 'no_test=true' "
            "means no test was found — fail in that case. "
            "'no_test=false' means a test exists — pass."
        ),
    },
]


def seed_uu_core_rubric(evaluator) -> str:
    """Create or update R-uu-core in the evaluator's rubric store.

    Idempotent — safe to call on every startup.
    Returns the rubric_id.
    """
    rubric_id = evaluator.rubric_create("uu-core", _UU_CORE_CRITERIA)
    log.info("seed_uu_core_rubric: seeded %s", rubric_id)
    return rubric_id


def _device_files() -> list[Path]:
    """Return device.py paths for all devices in the rack, sorted by name."""
    result = []
    for d in sorted(_DEVICES_ROOT.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
            device_file = d / "device.py"
            if device_file.exists():
                result.append(device_file)
    return result


def _test_exists(device_dir: Path) -> bool:
    """Return True if a smoke test file exists for this device."""
    name = device_dir.name
    norm = name.replace(
        "-", "_"
    )  # test files use underscores; device dirs may use hyphens
    tests_root = _UU_ROOT / "tests"
    candidates = [
        tests_root / f"test_{norm}_device.py",
        tests_root / f"test_{norm}.py",
        tests_root / f"test_{name}_device.py",
        tests_root / f"test_{name}.py",
        device_dir / "tests" / f"test_{norm}_device.py",
        device_dir / "tests" / f"test_{norm}.py",
    ]
    return any(c.exists() for c in candidates)


def _build_output(device_file: Path) -> str:
    """Build the evaluation 'output' text for a device file.

    Includes a no_test header line the rubric judges can read deterministically,
    followed by the device source code (trimmed to 6 KB).
    """
    code = device_file.read_text(errors="replace")[:6000]
    has_test = _test_exists(device_file.parent)
    return (
        f"Device: {device_file.parent.name}\n"
        f"no_test={'false' if has_test else 'true'}\n\n"
        f"{code}"
    )


def run_uu_enrollment(
    loop,
    rubric_id: str = UU_CORE_RUBRIC_ID,
    improve_threshold: float = 0.6,
) -> dict:
    """Run one UU self-enrollment cycle.

    Evaluates every device.py in devices/ against the given rubric.
    Low-scoring devices automatically receive a Platform improvement ticket
    (filed via cc_queue; Granny routes to CC on the next poll cycle).

    Returns a summary dict:
        {
          "devices_scanned": int,
          "tickets_filed": [ticket_id, ...],
          "results": [{"agent_id": ..., "score": ..., "verdict": ...,
                       "ticket_id": ..., "memory_id": ...}, ...],
        }
    """
    device_files = _device_files()
    results = []

    for device_file in device_files:
        agent_id = device_file.parent.name
        output = _build_output(device_file)
        try:
            cycle = loop.run_cycle(
                output=output,
                rubric_id=rubric_id,
                agent_id=agent_id,
                improve_threshold=improve_threshold,
            )
            results.append(
                {
                    "agent_id": agent_id,
                    "score": cycle["eval_result"]["score"],
                    "verdict": cycle["eval_result"]["verdict"],
                    "ticket_id": cycle["ticket_id"],
                    "memory_id": cycle["memory_id"],
                }
            )
            if cycle["ticket_id"]:
                log.info(
                    "uu_enrollment: %s → ticket %s (score=%.2f)",
                    agent_id,
                    cycle["ticket_id"],
                    cycle["eval_result"]["score"],
                )
        except Exception as e:
            log.warning("uu_enrollment: error evaluating %s: %s", agent_id, e)
            results.append({"agent_id": agent_id, "error": str(e)})

    tickets_filed = [r["ticket_id"] for r in results if r.get("ticket_id")]
    return {
        "devices_scanned": len(device_files),
        "results": results,
        "tickets_filed": tickets_filed,
    }
