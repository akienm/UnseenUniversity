"""T-intent-extractor-crash-masquerades-as-refusal — the proof.

INTENTION: a device's FAILURE is never indistinguishable from its HONEST REFUSAL.

THE HOLLOW BUILD THIS SUITE EXISTS TO FAIL: fix the `.get()`-on-a-list bug, watch
predictions start succeeding, declare done — and leave the NEXT crash silently
wearing CP1's clothes forever. Repairing the parse makes the crash RARE; it does
not make it VISIBLE, and rare-but-invisible is the worse of the two.

So the load-bearing fixture is not "does it parse". It is: run BOTH paths — a
CRASHING inference, and a model that legitimately answers `{"intent": "unknown",
"confidence": 0.7}` — and assert THE TWO STORED RECORDS DIFFER. Before the fix they
were byte-identical (`unknown`, and the crash's 0.0 confidence was the only tell,
which nothing read). A build that only fixes the parse fails this outright.

Hermetic: mocked store + mocked inference. No DB, no network, no live device.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock

from unseen_university.devices.intent import distribution as dist_mod
from unseen_university.devices.intent.device import IntentExtractorDevice
from unseen_university.devices.intent.store import IntentStore


def _device(dispatch_returns):
    """Device with a mocked store + an inference whose dispatch() is scripted.

    `dispatch_returns` is a list of str (the model's raw text) or Exception (raised).
    """
    inf = MagicMock()
    inf.dispatch.side_effect = [
        r if isinstance(r, Exception) else MagicMock(text=r) for r in dispatch_returns
    ]
    store = MagicMock(spec=IntentStore)
    store.get_few_shot_examples.return_value = []
    store.save_prediction.return_value = "pred-001"

    dev = IntentExtractorDevice.__new__(IntentExtractorDevice)
    dev._inference = inf
    dev._store = store
    dev._errors = []
    return dev, store, inf


def _saved(store) -> dict:
    """The record the device actually WROTE.

    Binds against the real ``save_prediction`` signature (defaults applied) so the
    record reads the same whether the device passes positionally or by keyword — the
    test asserts on the ROW THAT LANDS, not on the calling convention.
    """
    assert store.save_prediction.called, "no prediction was stored at all"
    args, kwargs = store.save_prediction.call_args
    bound = inspect.signature(IntentStore.save_prediction).bind(None, *args, **kwargs)
    bound.apply_defaults()
    rec = dict(bound.arguments)
    rec.pop("self", None)
    return rec


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


def test_a_crash_and_an_honest_refusal_are_stored_as_different_records():
    """The two-path discriminating fixture. THIS is what a hollow build cannot pass.

    Path A: the model emits a JSON LIST -> `parsed.get()` raises. A CRASH.
    Path B: the model honestly answers "I don't know" with real confidence. A REFUSAL.

    Both concern the string `unknown`. One is a bug; the other is CP1 working exactly
    as designed and is SACRED. If the stored records are identical, the system cannot
    tell its own failure from its own virtue — and neither can anyone reading the
    corpus, forever, because a record cannot be un-written.
    """
    # Path A — crashing inference (a list where a dict was expected).
    dev_a, store_a, _ = _device(['[{"intent": "refactor", "confidence": 0.9}]'])
    dev_a.predict(context="ctx", domain="coding")
    crash = _saved(store_a)

    # Path B — a model that genuinely, legitimately refuses.
    dev_b, store_b, _ = _device([json.dumps({"intent": "unknown", "confidence": 0.7})])
    dev_b.predict(context="ctx", domain="coding")
    refusal = _saved(store_b)

    assert crash != refusal, (
        "a CRASH and an HONEST REFUSAL stored the SAME record — the failure is "
        "wearing the virtue's clothes, which is the entire defect this ticket exists "
        f"to kill.\n  crash:   {crash}\n  refusal: {refusal}"
    )
    # NOT ENOUGH, and the reason matters. Pre-fix, these two records already differed
    # — by `confidence` (0.0 vs 0.7). That difference is ACCIDENTAL, not semantic: 0.0
    # is a legal confidence a model may genuinely return (see the test below), and
    # nothing downstream read it anyway. The record must STATE ITS CAUSE, not leave it
    # to be inferred from a coincidence of values.
    assert crash["provenance_class"] == "error", "a crash must be RECORDED as an error"
    assert refusal["provenance_class"] == "model", (
        "an honest 'I don't know' is a real ANSWER — never an error. Marking CP1's "
        "refusal a failure is the same conflation running the other way."
    )


def test_a_zero_confidence_model_answer_is_not_a_crash():
    """Kills the cheap fix: "just read the confidence column — 0.0 means it crashed."

    That was never a rule, only a coincidence. A model is entitled to answer with
    confidence 0.0 ("I'm certain of nothing here"), and the moment it does, the
    accidental discriminator collapses and a real answer is filed as a crash. Two
    causes, one signal — the SAME defect, merely rotated. Only a field that STATES
    the cause is injective, and only the write path can set it honestly.
    """
    dev, store, _ = _device([json.dumps({"intent": "unclear-scope", "confidence": 0.0})])
    dev.predict(context="ctx", domain="coding")
    rec = _saved(store)

    assert rec["provenance_class"] == "model", (
        "a model that answered with confidence 0.0 ANSWERED — it did not crash"
    )
    assert not rec["error_detail"]


def test_the_crash_record_carries_the_cause():
    """`error` alone is not enough — the record must say WHY, or the cause is lost."""
    # Both tiers return garbage: the escalation is exhausted, so this is a genuine,
    # final PARSE failure — not a masked one.
    dev, store, _ = _device(['[{"intent": "x"}]', '[{"intent": "x"}]'])
    dev.predict(context="ctx", domain="coding")
    rec = _saved(store)

    assert rec["error_detail"], "the crash record must carry the exception text"
    assert "parse" in rec["error_detail"].lower(), (
        "the FAILURE CLASS must survive into the record — a parse failure, a network "
        "failure and a timeout are different causes and must not collapse into one "
        "signal (that non-injective map is the defect, restated)."
    )


def test_an_inference_failure_and_a_parse_failure_are_different_causes():
    """The bare `except Exception` collapsed every failure class into one value."""
    dev_p, store_p, _ = _device(['[{"intent": "x"}]'] * 2)      # parse failure, both tiers
    dev_p.predict(context="ctx", domain="coding")

    dev_i, store_i, _ = _device([RuntimeError("connection refused")])  # inference failure
    dev_i.predict(context="ctx", domain="coding")

    assert _saved(store_p)["error_detail"] != _saved(store_i)["error_detail"], (
        "a parse failure and an unreachable model must not write the same record"
    )
    assert "connection refused" in _saved(store_i)["error_detail"]


def test_unparseable_output_escalates_a_tier_before_giving_up():
    """A device that cannot parse its model's answer and has no recourse has no error
    HANDLING — only an error MASK. The root cause is upstream of the parse: a
    minion-tier model handed a 10-example prompt returns a list. Escalate, then fail.
    """
    dev, store, inf = _device([
        '[{"intent": "refactor"}]',                                    # minion: garbage
        json.dumps({"intent": "refactor", "confidence": 0.9}),         # escalated: valid
    ])
    out = dev.predict(context="ctx", domain="coding")

    assert inf.dispatch.call_count == 2, "unparseable output must be RETRIED, not masked"
    first, second = [c.args[0] for c in inf.dispatch.call_args_list]
    assert first.task_class == "minion"
    assert second.task_class != "minion", "the retry must ESCALATE, not repeat the tier"

    assert out["intent"] == "refactor"
    assert _saved(store)["provenance_class"] == "model"


def test_a_recovered_escalation_is_not_recorded_as_an_error():
    """Sanity on the other side: escalation that SUCCEEDS is a success, not a failure."""
    dev, store, _ = _device([
        "not json at all",
        json.dumps({"intent": "add-tests", "confidence": 0.8}),
    ])
    dev.predict(context="ctx", domain="coding")
    assert _saved(store)["provenance_class"] == "model"
    assert _saved(store)["predicted_intent"] == "add-tests"


def test_both_tiers_failing_still_records_an_error_not_an_unknown():
    dev, store, _ = _device(["garbage", "still garbage"])
    dev.predict(context="ctx", domain="coding")
    assert _saved(store)["provenance_class"] == "error"


# ── the distribution monitor ──────────────────────────────────────────────────


def _dist_store(dist):
    s = MagicMock(spec=IntentStore)
    s.output_distribution.return_value = dist
    return s


def test_distribution_monitor_fires_when_a_window_is_degenerate(monkeypatch):
    """The gap NO record-level check closes.

    Every one of the 2,435 bad records was individually WELL-FORMED. Shape checks
    would have passed all of them. Only the distribution screamed.
    """
    fired = []
    monkeypatch.setattr(dist_mod, "raise_alarm", lambda **kw: fired.append(kw))

    store = _dist_store(
        {"samples": 100, "distinct": 2, "top_value": "unknown", "top_share": 0.97}
    )
    verdict = dist_mod.check_output_distribution(store, domain="coding")

    assert verdict["fired"] is True
    assert verdict["reason"] == "degenerate-output"
    assert fired, "a degenerate distribution must RAISE — a verdict nobody reads is no check"
    assert "unknown" in fired[0]["message"]


def test_distribution_monitor_is_silent_on_a_healthy_window(monkeypatch):
    fired = []
    monkeypatch.setattr(dist_mod, "raise_alarm", lambda **kw: fired.append(kw))

    store = _dist_store(
        {"samples": 100, "distinct": 14, "top_value": "refactor", "top_share": 0.22}
    )
    verdict = dist_mod.check_output_distribution(store, domain="coding")

    assert verdict["fired"] is False and verdict["reason"] == "ok"
    assert not fired


def test_too_few_samples_reports_undetermined_never_healthy(monkeypatch):
    """`insufficient-samples` is NOT `ok`. An absent check must never read as a
    passing one — that equivalence is the ossification hazard in miniature."""
    monkeypatch.setattr(dist_mod, "raise_alarm", lambda **kw: None)
    store = _dist_store(
        {"samples": 3, "distinct": 1, "top_value": "unknown", "top_share": 1.0}
    )
    verdict = dist_mod.check_output_distribution(store, domain="coding")

    assert verdict["fired"] is False
    assert verdict["reason"] == "insufficient-samples", (
        "3-of-3 identical is a Tuesday, not an outage — but it is UNDETERMINED, not OK"
    )


def test_the_monitor_runs_on_the_live_predict_path(monkeypatch):
    """A monitor that only fires inside pytest IS the defect this ticket kills.

    It must be wired to the real path, or it is a check indistinguishable from an
    absent one — exactly the thing we are here to make impossible.
    """
    calls = []
    monkeypatch.setattr(
        "unseen_university.devices.intent.device.check_output_distribution",
        lambda store, domain, **kw: calls.append(domain) or {"fired": False},
    )
    dev, _, _ = _device([json.dumps({"intent": "refactor", "confidence": 0.9})])
    dev._monitor_every = 1   # sample every prediction rather than every 25th
    dev.predict(context="ctx", domain="coding")

    assert calls == ["coding"], "predict() must exercise the distribution monitor"


# ── the corpus must survive the cleanup ───────────────────────────────────────


def test_the_few_shot_corpus_never_reads_predicted_intent():
    """The crash poisoned the ANSWER, not the QUESTION.

    Over-correcting here would be its own disaster: the ~2,435 crash rows still carry
    ~2,435 perfectly good training pairs, because the pair is (ticket description ->
    HUMAN-declared intention) and neither side is the crashed prediction. Filtering
    error rows out of the corpus would destroy it in the name of cleaning it.
    """
    src = inspect.getsource(IntentStore.get_few_shot_examples)

    assert "actual_outcome" in src, "the corpus outcome is the human label"
    assert "predicted_intent" not in src.split('"""')[-1], (
        "the few-shot corpus must NEVER feed the model its own predictions back as "
        "ground truth — and must never be filtered on them either"
    )
    assert "provenance_class" not in src.split('"""')[-1], (
        "error rows are NOT excluded from the corpus — see the docstring; excluding "
        "them would throw away every training pair the crashed predictions carried"
    )
