"""Tests for devices/evaluator/device.py — EvaluatorDevice.

Unit tests mock psycopg2 and InferenceDevice. Integration tests are gated on
UU_HOME_DB_URL being set; they hit real Postgres and real inference.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch, call

import pytest

from unseen_university.devices.evaluator.device import EvaluatorDevice, _extract_json
from unseen_university.devices.evaluator.shim import EvaluatorShim

# ── Helpers ───────────────────────────────────────────────────────────────────

_PG_URL = os.environ.get("UU_HOME_DB_URL", "")
_SKIP_INTEGRATION = pytest.mark.skipif(
    not _PG_URL, reason="UU_HOME_DB_URL not set — skipping integration tests"
)


def _judge_response(
    passed: bool = True, criteria_names: list[str] | None = None
) -> str:
    names = criteria_names or ["coherent"]
    criteria_results = [
        {"name": n, "passed": passed, "reasoning": "looks good"} for n in names
    ]
    return json.dumps({"overall_passed": passed, "criteria_results": criteria_results})


def _mock_inference(responses: list[str]):
    """Return a mock InferenceDevice whose dispatch() returns responses in order."""
    inf = MagicMock()
    inf.dispatch.side_effect = [MagicMock(text=r) for r in responses]
    return inf


def _mock_palace_row(rubric_id: str, criteria: list[dict]):
    """A fetchone() result for a palace rubric row."""
    content = json.dumps({"name": rubric_id, "criteria": criteria})
    return (content,)


def _mock_conn(fetchone_val=None, fetchall_val=None):
    """Build a mock psycopg2 connection with cursor context manager."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_val
    cur.fetchall.return_value = fetchall_val or []
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


# ── _extract_json ─────────────────────────────────────────────────────────────


def test_extract_json_plain():
    data = _extract_json('{"overall_passed": true, "criteria_results": []}')
    assert data["overall_passed"] is True


def test_extract_json_markdown_fence():
    text = '```json\n{"overall_passed": false, "criteria_results": []}\n```'
    data = _extract_json(text)
    assert data["overall_passed"] is False


def test_extract_json_fence_no_lang():
    text = '```\n{"overall_passed": true}\n```'
    data = _extract_json(text)
    assert data["overall_passed"] is True


def test_extract_json_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("not json")


# ── EvaluatorShim ─────────────────────────────────────────────────────────────


def test_shim_device_id():
    s = EvaluatorShim()
    assert s.device_id == "evaluator"


def test_shim_self_test():
    s = EvaluatorShim()
    result = s.self_test()
    assert result["passed"] is True
    assert "details" in result


# ── EvaluatorDevice construction ──────────────────────────────────────────────


def test_device_constructs_without_args():
    """__init__ must succeed with no env vars and no args (no I/O at construction)."""
    d = EvaluatorDevice()
    assert d.DEVICE_ID == "evaluator"


def test_device_constructs_with_injected_deps():
    inf = MagicMock()
    d = EvaluatorDevice(inference_device=inf, db_url="postgresql://fake")
    assert d._inference is inf


def test_who_am_i_shape():
    d = EvaluatorDevice()
    w = d.who_am_i()
    assert w["device_id"] == "evaluator"
    assert "name" in w
    assert "version" in w


def test_health_degraded_without_db_url(monkeypatch):
    monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
    d = EvaluatorDevice()
    h = d.health()
    assert h["status"] == "degraded"
    assert "UU_HOME_DB_URL" in h["detail"]


def test_health_healthy_with_db_url():
    d = EvaluatorDevice(db_url="postgresql://fake")
    h = d.health()
    assert h["status"] == "healthy"


def test_interface_version():
    from unseen_university.device import INTERFACE_VERSION

    d = EvaluatorDevice()
    assert d.interface_version() == INTERFACE_VERSION


# ── rubric_create ─────────────────────────────────────────────────────────────


def test_rubric_create_returns_rubric_id():
    conn, cur = _mock_conn()
    d = EvaluatorDevice(db_url="postgresql://fake")
    with patch(
        "unseen_university.devices.evaluator.device.psycopg2" if False else "psycopg2.connect",
        return_value=conn,
    ):
        with patch.object(d, "_db_connect", return_value=conn):
            rid = d.rubric_create(
                "basic", [{"name": "coherent", "instruction": "Is it coherent?"}]
            )
    assert rid == "R-basic"


def test_rubric_create_slugifies_name():
    conn, cur = _mock_conn()
    d = EvaluatorDevice(db_url="postgresql://fake")
    with patch.object(d, "_db_connect", return_value=conn):
        rid = d.rubric_create("My Rubric Name!", [])
    assert rid == "R-my-rubric-name"


def test_rubric_create_upserts_to_palace():
    conn, cur = _mock_conn()
    d = EvaluatorDevice(db_url="postgresql://fake")
    criteria = [{"name": "coherent", "instruction": "Is it coherent?"}]
    with patch.object(d, "_db_connect", return_value=conn):
        d.rubric_create("basic", criteria)
    # Verify INSERT was called with palace path
    assert cur.execute.called
    sql, params = cur.execute.call_args[0]
    assert "adc.palace" in sql
    assert params[0] == "evaluator.rubric.R-basic"
    assert params[1] == "basic"


# ── rubric_list ───────────────────────────────────────────────────────────────


def test_rubric_list_returns_empty_on_db_error():
    d = EvaluatorDevice(db_url="postgresql://fake")
    with patch.object(d, "_db_connect", side_effect=RuntimeError("no db")):
        result = d.rubric_list()
    assert result == []


def test_rubric_list_parses_palace_rows():
    from datetime import timezone, datetime as dt

    criteria = [{"name": "coherent", "instruction": "Is it coherent?"}]
    content = json.dumps({"name": "basic", "criteria": criteria})
    updated = dt(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    conn, cur = _mock_conn(
        fetchall_val=[("evaluator.rubric.R-basic", "basic", content, updated)]
    )
    d = EvaluatorDevice(db_url="postgresql://fake")
    with patch.object(d, "_db_connect", return_value=conn):
        result = d.rubric_list()
    assert len(result) == 1
    assert result[0]["rubric_id"] == "R-basic"
    assert result[0]["name"] == "basic"
    assert result[0]["criteria"] == criteria


# ── evaluate ──────────────────────────────────────────────────────────────────


def _evaluator_with_rubric(criteria, judge_responses):
    """Build an EvaluatorDevice wired with mocked DB (rubric loaded) and inference."""
    content = json.dumps({"name": "basic", "criteria": criteria})

    # DB: first connect for _load_rubric, then for _ensure_eval_history, then for insert
    rubric_conn, rubric_cur = _mock_conn(fetchone_val=(content,))
    ensure_conn, _ = _mock_conn()
    insert_conn, _ = _mock_conn()

    inf = _mock_inference(judge_responses)
    d = EvaluatorDevice(inference_device=inf, db_url="postgresql://fake")

    conn_sequence = [rubric_conn, ensure_conn, insert_conn]
    d._db_connect = lambda: conn_sequence.pop(0)
    return d


def test_evaluate_returns_three_judge_entries():
    criteria = [{"name": "coherent", "instruction": "Is it coherent?"}]
    responses = [_judge_response(True, ["coherent"]) for _ in range(3)]
    d = _evaluator_with_rubric(criteria, responses)
    result = d.evaluate("hello world", "R-basic", agent_id="test-agent")
    assert len(result["judge_reasoning"]) == 3


def test_evaluate_majority_pass():
    criteria = [{"name": "coherent"}]
    # 2 pass, 1 fail → majority pass
    responses = [_judge_response(True), _judge_response(True), _judge_response(False)]
    d = _evaluator_with_rubric(criteria, responses)
    result = d.evaluate("hello world", "R-basic")
    assert result["verdict"] == "pass"


def test_evaluate_majority_fail():
    criteria = [{"name": "coherent"}]
    responses = [_judge_response(False), _judge_response(False), _judge_response(True)]
    d = _evaluator_with_rubric(criteria, responses)
    result = d.evaluate("hello world", "R-basic")
    assert result["verdict"] == "fail"


def test_evaluate_result_shape():
    criteria = [{"name": "coherent"}]
    responses = [_judge_response(True) for _ in range(3)]
    d = _evaluator_with_rubric(criteria, responses)
    result = d.evaluate("hello world", "R-basic", agent_id="myagent")
    assert result["agent_id"] == "myagent"
    assert result["rubric_id"] == "R-basic"
    assert "score" in result
    assert "verdict" in result
    assert "eval_id" in result
    assert "evaluated_at" in result
    assert isinstance(result["judge_reasoning"], list)


def test_evaluate_failed_judge_still_produces_entry():
    """A judge that throws must still produce an entry — no dropped judges."""
    criteria = [{"name": "coherent"}]
    inf = MagicMock()
    # First two judges succeed; third raises
    inf.dispatch.side_effect = [
        MagicMock(text=_judge_response(True, ["coherent"])),
        MagicMock(text=_judge_response(True, ["coherent"])),
        RuntimeError("judge exploded"),
    ]
    content = json.dumps({"name": "basic", "criteria": criteria})
    rubric_conn, _ = _mock_conn(fetchone_val=(content,))
    ensure_conn, _ = _mock_conn()
    insert_conn, _ = _mock_conn()

    d = EvaluatorDevice(inference_device=inf, db_url="postgresql://fake")
    conn_sequence = [rubric_conn, ensure_conn, insert_conn]
    d._db_connect = lambda: conn_sequence.pop(0)

    result = d.evaluate("hello world", "R-basic")
    assert len(result["judge_reasoning"]) == 3
    # Third judge should record the error
    assert result["judge_reasoning"][2]["passed"] is False
    assert "error" in result["judge_reasoning"][2]["raw_response"]


def test_evaluate_raises_on_missing_rubric():
    conn, cur = _mock_conn(fetchone_val=None)  # rubric not found
    d = EvaluatorDevice(db_url="postgresql://fake")
    with patch.object(d, "_db_connect", return_value=conn):
        with pytest.raises(ValueError, match="not found"):
            d.evaluate("hello world", "R-nonexistent")


def test_evaluate_stores_in_eval_history():
    criteria = [{"name": "coherent"}]
    responses = [_judge_response(True) for _ in range(3)]
    content = json.dumps({"name": "basic", "criteria": criteria})

    rubric_conn, _ = _mock_conn(fetchone_val=(content,))
    ensure_conn, _ = _mock_conn()
    insert_conn, insert_cur = _mock_conn()

    inf = _mock_inference(responses)
    d = EvaluatorDevice(inference_device=inf, db_url="postgresql://fake")
    conn_sequence = [rubric_conn, ensure_conn, insert_conn]
    d._db_connect = lambda: conn_sequence.pop(0)

    d.evaluate("hello world", "R-basic", agent_id="myagent")
    # INSERT into eval_history was executed
    assert insert_cur.execute.called
    sql, params = insert_cur.execute.call_args[0]
    assert "eval_history" in sql
    assert params[1] == "myagent"


# ── eval_history ──────────────────────────────────────────────────────────────


def test_eval_history_returns_empty_on_db_error():
    d = EvaluatorDevice(db_url="postgresql://fake")
    conn, _ = _mock_conn()
    with patch.object(d, "_db_connect", side_effect=[conn, RuntimeError("no table")]):
        result = d.eval_history("myagent")
    assert result == []


def test_eval_history_parses_rows():
    from datetime import timezone, datetime as dt

    eva = dt(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    judges = [{"judge_index": i, "passed": True, "score": 1.0} for i in range(3)]
    rows = [("E-abc123", "myagent", "R-basic", 1.0, "pass", judges, eva)]
    ensure_conn, _ = _mock_conn()
    query_conn, _ = _mock_conn(fetchall_val=rows)

    d = EvaluatorDevice(db_url="postgresql://fake")
    conn_sequence = [ensure_conn, query_conn]
    d._db_connect = lambda: conn_sequence.pop(0)

    result = d.eval_history("myagent")
    assert len(result) == 1
    assert result[0]["eval_id"] == "E-abc123"
    assert result[0]["verdict"] == "pass"
    assert len(result[0]["judge_reasoning"]) == 3


# ── Integration tests (real DB + inference) ───────────────────────────────────


@_SKIP_INTEGRATION
def test_integration_rubric_roundtrip():
    """Create a rubric and retrieve it from real Postgres."""
    d = EvaluatorDevice(db_url=_PG_URL)
    criteria = [{"name": "not_empty", "instruction": "Is the output non-empty?"}]
    rid = d.rubric_create("integration-test-rubric", criteria)
    assert rid.startswith("R-")

    rubrics = d.rubric_list()
    ids = [r["rubric_id"] for r in rubrics]
    assert rid in ids


@_SKIP_INTEGRATION
def test_integration_evaluate_returns_three_judges():
    """Evaluate against a real rubric with real inference (mocked inference via DI)."""
    d = EvaluatorDevice(db_url=_PG_URL)
    criteria = [{"name": "coherent", "instruction": "Is the output coherent?"}]
    d.rubric_create("basic", criteria)

    inf = _mock_inference([_judge_response(True, ["coherent"]) for _ in range(3)])
    d._inference = inf

    result = d.evaluate("hello world", "R-basic", agent_id="integration-test")
    assert len(result["judge_reasoning"]) == 3
    assert result["verdict"] in ("pass", "fail")

    history = d.eval_history("integration-test", limit=5)
    assert any(r["rubric_id"] == "R-basic" for r in history)
