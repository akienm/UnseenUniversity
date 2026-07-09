"""
An inference request is its own JSON ticket — the whole escalation, readable at a glance.

T-inference-run-record.

Why
---
`io_corpus` captures every byte of every model CALL. Nothing captures a REQUEST. One request is an
escalation walk — a cheap local model fails on capability, the walk spends up a rung, the next one
answers — and those hops are two records among 1948, joined only by fields you must already know to
grep for.

The motivating failure is exact: CC went looking for a truncated reply, searched the memory store,
found nothing, and filed a ticket asserting the data did not exist — while 19,216 characters of it
sat in the corpus on disk. The capture was never the problem; findability was.

Hermetic
--------
Drives the REAL `BaseDomain.run` escalation walk through `GeneralDomain` with a STUB device. Never
constructs an `InferenceDevice` — its `HealthMonitor` probes live providers, so a proof built on one
passes or fails on the weather (2026-07-08). `UU_INFERENCE_CORPUS` redirects the corpus root, so no
test ever writes to `~/.unseen_university`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from unseen_university.devices.inference.domains.general import GeneralDomain
from unseen_university.devices.inference.run_record import (
    RESOLUTION_AVAILABILITY_EXHAUSTED,
    RESOLUTION_CAPABILITY_CEILING,
    RESOLUTION_DONE,
    RETENTION_DAYS,
    SCHEMA,
    runs_root,
)

#: The model's scratchpad. It must NEVER reach the run record — only the claim crosses.
#: NB it deliberately does NOT contain the correct answer. The first draft said "then check 43
#: carefully", so a hop-0 reply whose ANSWER was 41 still satisfied a `"43" in text` check — the
#: scratchpad leaked the answer past the verifier. That is the same defect measured in the corpus
#: query b4-boxes, whose prompt contains its own correct answer token. Fixtures leak answers too.
REASONING = "Let me think. 6a+9b+20c... I'll try a few values and check each one carefully."


@dataclass
class _StubResponse:
    text: str
    finish_reason: str = "stop"
    source_kind: str = "local"
    model: str = "stub-model"
    input_tokens: int = 11
    output_tokens: int = 22
    cost_estimate: float = 0.5
    source_billing_type: str = "usage_based"
    tool_calls: list | None = None
    raw: dict = field(default_factory=dict)
    elapsed_ms: int = 0


@dataclass
class _StubDevice:
    """Replies per escalation hop. No network, no health probe, no provider."""

    replies: dict[int, object]
    seen: list = field(default_factory=list)

    def dispatch(self, request):
        self.seen.append(request)
        reply = self.replies[request.escalation_hop]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def _corpus_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path / "corpus"))
    return tmp_path


def _records() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(runs_root().rglob("run_*.json"))]


def _domain(replies, answer_check):
    return GeneralDomain(
        inference_device=_StubDevice(replies=replies),
        answer_check=answer_check,
    )


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


def test_an_escalated_request_leaves_one_run_record_naming_every_hop():
    """Two rungs, one record: hop 0 failed on capability, hop 1 answered.

    This is the whole point — the escalation read at a glance, without grepping NDJSON.
    """
    wrong = _StubResponse(text=f"{REASONING}\nANSWER: 41")
    right = _StubResponse(text=f"{REASONING}\nANSWER: 43", model="stub-bigger")
    domain = _domain({0: wrong, 1: right}, answer_check=lambda t: "43" in t)

    answer = domain.ask("largest number of units you cannot buy?", query_id="T-frob")
    assert answer is not None, "the stub's hop-1 reply satisfies the check; the walk must finish"

    records = _records()
    assert len(records) == 1, f"exactly one run record per request; got {len(records)}"
    rec = records[0]

    assert rec["schema"] == SCHEMA
    assert rec["resolution"] == RESOLUTION_DONE
    assert rec["ticket_id"] == "T-frob"
    assert rec["summary"] is None, "summary starts null — it is written once, after analysis"
    assert rec["hops_used"] == 2 and len(rec["hops"]) == 2

    h0, h1 = rec["hops"]
    assert h0["hop"] == 0 and h1["hop"] == 1, "hops are recorded in walk order"
    assert h0["classification"] == "capability", (
        "hop 0 finished but did not satisfy the check — that is the CAPABILITY failure "
        "which is the only thing licensed to spend up a tier"
    )
    assert h1["classification"] == "done"
    assert h0["required_difficulty"] != h1["required_difficulty"], (
        "the walk must bump a difficulty rung between hops, or nothing was escalated"
    )
    assert h1["model"] == "stub-bigger", "the record must name WHICH model answered"

    # 30-day lifespan, stamped at write.
    ts = datetime.fromisoformat(rec["ts"])
    assert datetime.fromisoformat(rec["expires_at"]) - ts == timedelta(days=RETENTION_DAYS)


def test_a_walk_that_never_finishes_still_leaves_a_record_saying_so():
    """THE DISCRIMINATOR. A record written only on success would pass the test above.

    The runs worth reading are the ones that failed. A halted walk must leave a record, and its
    resolution must name WHY it stopped — never `done`.
    """
    never = _StubResponse(text=f"{REASONING}\nANSWER: 41")
    domain = _domain({h: never for h in range(6)}, answer_check=lambda t: False)

    answer = domain.ask("unanswerable", query_id="T-halt")
    assert answer is None, "no hop satisfies the check — the walk must halt"

    records = _records()
    assert len(records) == 1, (
        "a halted walk left NO run record. The failures are precisely the runs someone opens; "
        "a record written only on the success path is the least useful artifact possible."
    )
    rec = records[0]
    assert rec["resolution"] == RESOLUTION_CAPABILITY_CEILING, (
        f"a walk that escalated past the top rung must say so, not {rec['resolution']!r}"
    )
    assert rec["resolution"] != RESOLUTION_DONE
    assert rec["hops_used"] >= 2, "the record must show every rung that was tried and failed"
    assert all(h["classification"] == "capability" for h in rec["hops"])


def test_the_record_references_the_payload_and_never_duplicates_it():
    """Akien: 'the complete querys and replies don't even have to be in the ticket.'

    Two copies of a payload means two truths, and the bigger one rots first. The record excerpts
    the question and carries the CLAIM; the bytes live in io_corpus.
    """
    long_question = "why? " + ("padding " * 400)   # ~3200 chars
    reply = _StubResponse(text=f"{REASONING}\nANSWER: 43")
    domain = _domain({0: reply}, answer_check=lambda t: "43" in t)
    domain.ask(long_question, query_id="T-big")

    rec = _records()[0]
    assert len(rec["question"]) < len(long_question), "the question must be excerpted, not stored whole"
    assert len(rec["question"]) <= 500

    blob = json.dumps(rec)
    assert REASONING not in blob, (
        "the model's REASONING leaked into the run record. Only the failed CLAIM crosses a rung, "
        "and only the claim belongs in the glance-view — the derivation is what produced the "
        "wrong answer in the first place."
    )
    assert rec["hops"][0]["claim"] == "43", "the record carries the extracted claim, not the scratchpad"


def test_a_source_that_is_down_never_spends_up_a_tier_in_the_record():
    """AVAILABILITY must never look like CAPABILITY in the record — that split IS the money safety.

    A down source re-selects at the SAME rung (escalation_hop stays 0) and spends nothing; only a
    capability failure bumps. So a walk that only ever meets a down source records N availability
    hops all at one difficulty, and halts availability-exhausted. If the record blurred the two, a
    reader auditing spend would see tier bumps that never happened — and 'Hex is down' would read
    as 'the cheap model wasn't good enough', which is how you talk yourself into paying more.
    """
    down = _StubResponse(text="", finish_reason="error", source_kind="none")
    domain = _domain({0: down}, answer_check=lambda t: "43" in t)
    assert domain.ask("q", query_id="T-down") is None

    rec = _records()[0]
    assert rec["resolution"] == RESOLUTION_AVAILABILITY_EXHAUSTED, (
        f"a walk that never reached a live source must say so, not {rec['resolution']!r}"
    )
    assert rec["hops"], "the record must show the attempts made against the down source"
    assert all(h["classification"] == "availability" for h in rec["hops"]), (
        "a source that never came up is an AVAILABILITY failure — the one class that must not "
        f"spend up a tier; got {[h['classification'] for h in rec['hops']]}"
    )
    assert len({h["required_difficulty"] for h in rec["hops"]}) == 1, (
        "availability re-selects at the SAME difficulty; a bump here would be a paid escalation "
        "triggered by a down box"
    )
    assert rec["total_dollars"] == 0.0, "a down source bills nothing"
