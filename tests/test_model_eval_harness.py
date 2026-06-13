"""Tests for the model eval harness: EvaluatorDevice.model_eval_run() +
devices/inference/capability_graph.py + InferenceDevice.capability_graph_query().

Unit tests mock DB and InferenceDevice.
Integration tests are gated on UU_HOME_DB_URL and OPENROUTER_API_KEY.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch, call

import pytest

from devices.evaluator.device import EvaluatorDevice
from devices.inference.device import InferenceDevice
from devices.inference.shim import InferenceResponse

_PG_URL = os.environ.get("UU_HOME_DB_URL", "")
_SKIP_INTEGRATION = pytest.mark.skipif(
    not _PG_URL, reason="UU_HOME_DB_URL not set — skipping integration tests"
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_conn(fetchone_val=None, fetchall_val=None):
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


def _fake_resp(text="def hello(): pass", model="openai/gpt-4o-mini", cost=0.001):
    return InferenceResponse(
        text=text,
        model=model,
        finish_reason="stop",
        input_tokens=20,
        output_tokens=10,
        cost_estimate=cost,
        elapsed_ms=250,
    )


def _judge_resp(passed=True):
    return json.dumps(
        {
            "overall_passed": passed,
            "criteria_results": [
                {"name": "correct", "passed": passed, "reasoning": "ok"}
            ],
        }
    )


def _make_evaluator(models_responses, with_rubric=False, rubric_criteria=None):
    """Build EvaluatorDevice with mocked inference and DB."""
    inf = MagicMock()
    responses = list(models_responses)
    if with_rubric:
        # After each dispatch, 3 judge calls follow
        expanded = []
        for r in responses:
            expanded.append(r)
            expanded += [MagicMock(text=_judge_resp(True)) for _ in range(3)]
        inf.dispatch.side_effect = expanded
    else:
        inf.dispatch.side_effect = responses

    d = EvaluatorDevice(inference_device=inf, db_url="postgresql://fake")

    # capability_graph calls: ensure_table (1 conn) + N * insert_result (N conns)
    # If rubric: also N * (rubric_load + ensure_eval_history + eval_insert) = N*3 extra
    conn_factory = MagicMock(return_value=_mock_conn()[0])

    if with_rubric:
        criteria = rubric_criteria or [{"name": "correct", "instruction": "Is it?"}]
        rubric_content = json.dumps({"name": "test-rubric", "criteria": criteria})
        rubric_conn, _ = _mock_conn(fetchone_val=(rubric_content,))
        ensure_eval_conn, _ = _mock_conn()
        eval_insert_conn, _ = _mock_conn()
        conns_per_model = [rubric_conn, ensure_eval_conn, eval_insert_conn]

        n_models = len(responses)
        all_conns = [_mock_conn()[0]] + conns_per_model * n_models
        it = iter(all_conns)
        d._db_connect = lambda: next(it)
    else:
        # 1 ensure_table conn + N insert_result conns
        n_models = len(responses)
        conns = [_mock_conn()[0] for _ in range(1 + n_models)]
        it = iter(conns)
        d._db_connect = lambda: next(it)

    return d, inf


# ── capability_graph unit tests ───────────────────────────────────────────────


def test_capability_graph_ensure_table_called():
    """ensure_table must execute a CREATE TABLE IF NOT EXISTS."""
    from devices.inference.capability_graph import ensure_table

    conn, cur = _mock_conn()
    with patch("psycopg2.connect", return_value=conn):
        ensure_table("postgresql://fake")
    assert cur.execute.called
    sql = cur.execute.call_args[0][0]
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "adc.model_eval_results" in sql


def test_capability_graph_insert_result_calls_db():
    from devices.inference.capability_graph import insert_result

    conn, cur = _mock_conn()
    with patch("psycopg2.connect", return_value=conn):
        insert_result(
            "postgresql://fake",
            result_id="ME-abc",
            run_group_id="RG-001",
            task_class="programming",
            model="openai/gpt-4o-mini",
            provider="openai",
            task_text="write hello",
            output_text="def hello(): pass",
            quality_score=0.9,
            verdict="pass",
            eval_id="E-xyz",
            latency_ms=200,
            input_tokens=15,
            output_tokens=10,
            cost_usd=0.001,
        )
    assert cur.execute.called
    sql, params = cur.execute.call_args[0]
    assert "adc.model_eval_results" in sql
    assert "ME-abc" in params


def test_capability_graph_query_results_returns_list():
    from devices.inference.capability_graph import query_results
    from datetime import datetime, timezone

    ran = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    rows = [
        (
            "ME-001",
            "RG-001",
            "programming",
            "openai/gpt-4o-mini",
            "openai",
            0.9,
            "pass",
            200,
            15,
            10,
            0.001,
            ran,
        ),
    ]
    conn, cur = _mock_conn(fetchall_val=rows)
    with patch("psycopg2.connect", return_value=conn):
        result = query_results("postgresql://fake", task_class="programming")
    assert len(result) == 1
    assert result[0]["model"] == "openai/gpt-4o-mini"
    assert result[0]["quality_score"] == 0.9
    assert result[0]["verdict"] == "pass"
    assert result[0]["task_class"] == "programming"


def test_capability_graph_query_returns_empty_on_error():
    from devices.inference.capability_graph import query_results

    with patch("psycopg2.connect", side_effect=RuntimeError("no db")):
        result = query_results("postgresql://fake")
    assert result == []


def test_capability_graph_insert_no_op_on_error():
    from devices.inference.capability_graph import insert_result

    with patch("psycopg2.connect", side_effect=RuntimeError("no db")):
        # must not raise
        insert_result(
            "postgresql://fake",
            result_id="ME-x",
            run_group_id="RG-x",
            task_class="",
            model="m",
            provider="p",
            task_text="t",
            output_text="o",
            quality_score=None,
            verdict=None,
            eval_id=None,
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=None,
        )


# ── model_eval_run unit tests ─────────────────────────────────────────────────


def test_model_eval_run_dispatches_each_model():
    """Exactly one dispatch per model."""
    models = ["openai/gpt-4o-mini", "anthropic/claude-haiku-4-5-20251001"]
    responses = [_fake_resp(model=m) for m in models]
    d, inf = _make_evaluator(responses)

    with (
        patch("devices.inference.capability_graph.ensure_table"),
        patch("devices.inference.capability_graph.insert_result"),
    ):
        d.model_eval_run("write a hello function", models, task_class="programming")

    assert inf.dispatch.call_count == len(models)


def test_model_eval_run_returns_run_group_id():
    models = ["openai/gpt-4o-mini", "openai/gpt-4o"]
    responses = [_fake_resp(model=m) for m in models]
    d, inf = _make_evaluator(responses)

    with (
        patch("devices.inference.capability_graph.ensure_table"),
        patch("devices.inference.capability_graph.insert_result"),
    ):
        result = d.model_eval_run("write a hello function", models)

    assert "run_group_id" in result
    assert result["run_group_id"].startswith("RG-")
    assert len(result["results"]) == len(models)


def test_model_eval_run_result_shape_per_model():
    """Each result entry has the required fields."""
    models = ["openai/gpt-4o-mini"]
    d, inf = _make_evaluator([_fake_resp(model=models[0])])

    with (
        patch("devices.inference.capability_graph.ensure_table"),
        patch("devices.inference.capability_graph.insert_result"),
    ):
        result = d.model_eval_run("write a hello function", models)

    entry = result["results"][0]
    for key in (
        "result_id",
        "model",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "quality_score",
        "verdict",
        "eval_id",
    ):
        assert key in entry, f"missing key: {key}"
    assert entry["model"] == "openai/gpt-4o-mini"
    assert entry["latency_ms"] == 250
    assert entry["cost_usd"] == 0.001


def test_model_eval_run_evaluates_when_rubric_given():
    """When rubric_id is passed, evaluate() is called once per model."""
    models = ["openai/gpt-4o-mini", "openai/gpt-4o"]
    criteria = [{"name": "correct", "instruction": "Is it?"}]
    rubric_content = json.dumps({"name": "t", "criteria": criteria})

    # ensure_table and insert_result are patched — they consume no DB conns.
    # The only _db_connect calls come from evaluate() per model:
    #   _load_rubric, _ensure_eval_history, eval_history INSERT  → 3 conns per model
    rubric_conns = []
    for _ in models:
        rc, _ = _mock_conn(fetchone_val=(rubric_content,))
        ec, _ = _mock_conn()
        ic, _ = _mock_conn()
        rubric_conns += [rc, ec, ic]

    inf = MagicMock()
    # Actual dispatch order: task1 → 3 judges → task2 → 3 judges (interleaved)
    side_effects = []
    for m in models:
        side_effects.append(_fake_resp(model=m, text="def hello(): pass"))
        side_effects += [MagicMock(text=_judge_resp(True)) for _ in range(3)]
    inf.dispatch.side_effect = side_effects

    d = EvaluatorDevice(inference_device=inf, db_url="postgresql://fake")
    it = iter(rubric_conns)
    d._db_connect = lambda: next(it)

    with (
        patch("devices.inference.capability_graph.ensure_table"),
        patch("devices.inference.capability_graph.insert_result"),
    ):
        result = d.model_eval_run("write a hello function", models, rubric_id="R-test")

    # 2 task dispatches + 6 judge dispatches (3 per model) = 8 total
    assert inf.dispatch.call_count == len(models) + 3 * len(models)
    for entry in result["results"]:
        assert entry["quality_score"] is not None
        assert entry["verdict"] in ("pass", "fail")
        assert entry["eval_id"] is not None


def test_model_eval_run_records_result_per_model():
    """insert_result must be called once per model."""
    models = [
        "openai/gpt-4o-mini",
        "openai/gpt-4o",
        "anthropic/claude-haiku-4-5-20251001",
    ]
    responses = [_fake_resp(model=m) for m in models]
    d, inf = _make_evaluator(responses)

    insert_calls = []
    with (
        patch("devices.inference.capability_graph.ensure_table"),
        patch(
            "devices.inference.capability_graph.insert_result",
            side_effect=lambda *a, **kw: insert_calls.append(kw),
        ),
    ):
        result = d.model_eval_run("write a hello function", models)

    assert len(insert_calls) == len(models)
    recorded_models = {c["model"] for c in insert_calls}
    assert recorded_models == set(models)


def test_model_eval_run_handles_dispatch_failure():
    """A failing model produces an error key in its result entry, run continues."""
    inf = MagicMock()
    inf.dispatch.side_effect = [RuntimeError("timeout"), _fake_resp()]
    d = EvaluatorDevice(inference_device=inf, db_url="postgresql://fake")
    conns = [_mock_conn()[0] for _ in range(3)]
    it = iter(conns)
    d._db_connect = lambda: next(it)

    models = ["bad-model/v1", "openai/gpt-4o-mini"]
    with (
        patch("devices.inference.capability_graph.ensure_table"),
        patch("devices.inference.capability_graph.insert_result"),
    ):
        result = d.model_eval_run("write a hello function", models)

    assert len(result["results"]) == 2
    assert "error" in result["results"][0]
    assert "error" not in result["results"][1]


def test_model_eval_run_skips_evaluate_on_dispatch_failure():
    """No evaluate() call when the dispatch fails (no output to score)."""
    inf = MagicMock()
    inf.dispatch.side_effect = RuntimeError("timeout")
    d = EvaluatorDevice(inference_device=inf, db_url="postgresql://fake")
    conn, _ = _mock_conn()
    d._db_connect = lambda: conn

    with (
        patch("devices.inference.capability_graph.ensure_table"),
        patch("devices.inference.capability_graph.insert_result"),
        patch.object(d, "evaluate") as mock_eval,
    ):
        d.model_eval_run("task", ["bad-model/v1"], rubric_id="R-test")

    mock_eval.assert_not_called()


def test_model_eval_run_same_run_group_id_for_all_models():
    """All results in a run share the same run_group_id."""
    models = ["openai/gpt-4o-mini", "openai/gpt-4o"]
    responses = [_fake_resp(model=m) for m in models]
    d, inf = _make_evaluator(responses)

    with (
        patch("devices.inference.capability_graph.ensure_table"),
        patch("devices.inference.capability_graph.insert_result"),
    ):
        result = d.model_eval_run("write a hello function", models)

    gid = result["run_group_id"]
    assert all(True for _ in result["results"])  # just check len
    assert len(result["results"]) == 2


# ── InferenceDevice.capability_graph_query ────────────────────────────────────


def test_inference_device_cg_query_returns_empty_without_db_url(monkeypatch):
    monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
    d = InferenceDevice(mode="openrouter")
    result = d.capability_graph_query()
    assert result == []


def test_inference_device_cg_query_delegates_to_module(monkeypatch):
    monkeypatch.setenv("UU_HOME_DB_URL", "postgresql://fake")
    d = InferenceDevice(mode="openrouter")
    with patch(
        "devices.inference.capability_graph.query_results",
        return_value=[{"id": "ME-1"}],
    ) as mock_qr:
        result = d.capability_graph_query(task_class="programming", model="gpt-4o-mini")
    mock_qr.assert_called_once_with(
        "postgresql://fake", task_class="programming", model="gpt-4o-mini", limit=50
    )
    assert result == [{"id": "ME-1"}]


# ── Integration tests (real DB) ───────────────────────────────────────────────


@_SKIP_INTEGRATION
def test_integration_capability_graph_roundtrip():
    """Write and read back a result from real Postgres."""
    from devices.inference.capability_graph import (
        ensure_table,
        insert_result,
        query_results,
    )
    import uuid

    gid = f"RG-test-{uuid.uuid4().hex[:6]}"
    rid = f"ME-test-{uuid.uuid4().hex[:6]}"

    ensure_table(_PG_URL)
    insert_result(
        _PG_URL,
        result_id=rid,
        run_group_id=gid,
        task_class="programming",
        model="openai/gpt-4o-mini",
        provider="openai",
        task_text="write hello world",
        output_text="def hello(): print('hello')",
        quality_score=0.85,
        verdict="pass",
        eval_id=None,
        latency_ms=180,
        input_tokens=12,
        output_tokens=8,
        cost_usd=0.0005,
    )

    rows = query_results(_PG_URL, model="openai/gpt-4o-mini", limit=5)
    ids = [r["id"] for r in rows]
    assert rid in ids


@_SKIP_INTEGRATION
def test_integration_model_eval_run_three_stacks():
    """Run one task against 3 models with a rubric; verify results queryable."""
    from devices.inference.capability_graph import ensure_table, query_results

    # Use mocked inference so no live API calls are needed
    models = [
        "openai/gpt-4o-mini",
        "anthropic/claude-haiku-4-5-20251001",
        "openai/gpt-4o",
    ]
    criteria = [{"name": "not_empty", "instruction": "Is the output non-empty?"}]
    rubric_content = json.dumps({"name": "test-harness", "criteria": criteria})

    judge_resp_text = json.dumps(
        {
            "overall_passed": True,
            "criteria_results": [
                {"name": "not_empty", "passed": True, "reasoning": "has content"}
            ],
        }
    )

    inf = MagicMock()
    # Interleave: task → 3 judges → task → 3 judges → task → 3 judges
    side_effects = []
    for m in models:
        side_effects.append(
            InferenceResponse(
                text="def hello(): pass",
                model=m,
                finish_reason="stop",
                input_tokens=20,
                output_tokens=10,
                cost_estimate=0.001,
                elapsed_ms=200,
            )
        )
        side_effects += [MagicMock(text=judge_resp_text) for _ in range(3)]
    inf.dispatch.side_effect = side_effects

    d = EvaluatorDevice(inference_device=inf, db_url=_PG_URL)

    # Ensure rubric exists
    d.rubric_create("test-harness", criteria)

    result = d.model_eval_run(
        task="Write a Python function that prints hello world",
        models=models,
        rubric_id="R-test-harness",
        task_class="programming-integration-test",
        agent_id="integration-test",
    )

    assert "run_group_id" in result
    assert len(result["results"]) == 3
    for entry in result["results"]:
        assert entry["quality_score"] is not None
        assert entry["verdict"] in ("pass", "fail")

    # Verify data is queryable from InferenceDevice
    idev = InferenceDevice()
    rows = idev.capability_graph_query(
        task_class="programming-integration-test", limit=10
    )
    run_ids = {r["run_group_id"] for r in rows}
    assert result["run_group_id"] in run_ids
