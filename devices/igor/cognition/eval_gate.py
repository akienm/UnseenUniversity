"""
eval_gate.py — Unified condition evaluator (T-condition-evaluator).

Single function `eval_gate(key, op, value, namespace)` shared by:
  - schema_runner.py  prim_branch / prim_if conditions (basket key-op-value)
  - basal_ganglia.py  D201 habit conditions (_conditions_match)
  - interpretive_edge conditions (TWM namespace) — future

Ops:
  ==  !=                     — string equality
  <  >  <=  >=               — numeric (float coerce; falls back to False)
  in  not_in                 — substring containment  (cmp_val in ctx_val)
  member_of  not_member_of   — scalar membership in a collection
  intersects                 — non-empty set intersection (both sides iterable)
"""

from __future__ import annotations

from typing import Any


def eval_gate(key: str, op: str, value: Any, namespace: dict) -> bool:
    """Evaluate namespace[key] <op> value → bool.

    Args:
        key:       Key to look up in namespace.
        op:        Comparison operator string (see module docstring).
        value:     Right-hand side to compare against.
        namespace: Dict providing the left-hand side.

    Returns True if the condition holds, False otherwise (never raises).
    """
    actual = namespace.get(key)

    if op == "==":
        return str(actual) == str(value)
    if op == "!=":
        return str(actual) != str(value)
    if op == "in":
        # cmp_val is substring of actual (string containment)
        return str(value) in str(actual if actual is not None else "")
    if op == "not_in":
        return str(value) not in str(actual if actual is not None else "")
    if op == "member_of":
        # actual is a scalar; value is a collection
        return actual in (value or [])
    if op == "not_member_of":
        return actual not in (value or [])
    if op == "intersects":
        # both sides are iterables; any overlap = True
        return bool(set(actual or []) & set(value or []))

    # Numeric ops
    try:
        a = float(actual)  # type: ignore[arg-type]
        b = float(value)
        if op == "<":
            return a < b
        if op == ">":
            return a > b
        if op == "<=":
            return a <= b
        if op == ">=":
            return a >= b
    except (ValueError, TypeError) as _exc:
        from .forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"eval_gate.py:65: {_exc}")

    return False
