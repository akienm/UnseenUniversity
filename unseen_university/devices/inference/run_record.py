"""
run_record.py — an inference request is its own JSON ticket.

T-inference-run-record. Akien, 2026-07-09: *"an inference request is its own kind of json ticket.
not just the calling parameters but everything that happens along the way… an inference call then
becomes the whole conversation of the escalation that you can read at a glance."*

Why this exists
---------------
`io_corpus` already captures every byte of every model CALL. What it does not have is a
REQUEST — the thing a human actually reasons about. One request is an escalation walk: hop 0
asks a cheap local model, it fails on capability, hop 1 spends up a rung, that one answers. Those
are two io records among 1948, joined only by fields you must know to grep for.

The failure that motivated this is exact and recent: CC went looking for a truncated reply,
searched the memory store, found nothing, and FILED A TICKET asserting the data did not exist —
while 19,216 characters of it sat in the corpus on disk. The capture was never the problem.
Findability was. Akien: *"the fact it didn't show up when you were looking at stuff is an
important lever."*

What this is NOT
----------------
It is not a second copy of the payloads. The full prompts and replies stay in `io_corpus`, which
holds every byte; this record REFERENCES them by `run_id` (every io record emitted during the walk
carries it) and truncates the question to a glance-sized excerpt. Two copies of a payload means
two truths, and the bigger one rots first.

The record is the INDEX and the NARRATIVE. The corpus is the evidence.

Lifecycle
---------
Written by ``BaseDomain.run`` — the single owner of the escalation walk — in a ``finally``, so a
halt, an exhausted walk, or an unexpected raise still leaves a record. **The failures are the runs
worth reading**; a record written only on success would be the least useful artifact imaginable.

`summary` starts null. An analyst (human or CC) writes a verdict into it once, and a run with a
non-null summary is never re-analyzed. That is the cache that makes a 30-day window cheap to
revisit. `expires_at` is stamped on write; the day-close sweeper prunes past it.

Fail-soft by contract, exactly like `io_corpus`: a record that cannot be written is logged and
swallowed. A lost record is bad; a crashed inference run is worse.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unseen_university.devices.inference.domains.reply_text import extract_answer
from unseen_university.devices.inference.io_corpus import corpus_root

log = logging.getLogger(__name__)

SCHEMA = "inference.run.v1"

#: "like all logging, they'd have a 30 day lifespan" (Akien). Stamped at write; swept at day-close.
RETENTION_DAYS = 30

#: The question is EXCERPTED, never stored whole — the full text lives in the io record this run
#: references. A reader wants to know which question, not to re-read it here.
QUESTION_EXCERPT_CHARS = 500

#: How a walk ended. `done` is the only success; every other value names WHY it stopped, because
#: a run that halted is the one someone will actually open.
RESOLUTION_DONE = "done"
RESOLUTION_CAPABILITY_CEILING = "capability_ceiling"      # escalated past the top rung, still no answer
RESOLUTION_AVAILABILITY_EXHAUSTED = "availability_exhausted"
RESOLUTION_AVAILABILITY_WALL = "availability_midrun_wall"
RESOLUTION_COST_CAP = "cost_cap"
RESOLUTION_HARVEST_WALL = "harvest_wall"                  # harvest policy: the wall is the wanted outcome
RESOLUTION_NO_ESCALATION_WALL = "no_escalation_wall"      # no-escalation policy: pinned single shot, silent halt
RESOLUTION_ERROR = "error"                                # an unexpected raise escaped the walk


def runs_root() -> Path:
    """Where run tickets live: a subdirectory of the corpus root they reference.

    Deliberately NOT a new top-level path under `uu_home()` (that directory is being cleaned, and
    a new path needs Akien's sign-off). Co-locating them means one place to discover and one place
    to prune, and the run's `run_id` joins straight into the io records beside it.
    """
    return corpus_root() / "runs"


@dataclass
class HopRecord:
    """One rung of the escalation walk: who was asked, what they said, and why we moved on.

    `claim` is the model's ANSWER, extracted — never its reasoning. That is the same discipline
    the escalation handoff enforces (only the failed CLAIM crosses a rung, never the argument for
    it), and for the same reason: a reader skimming a failed run should see what was asserted, not
    be dragged through the derivation that produced it.
    """

    hop: int
    required_difficulty: str
    outcome: str            # the raw LoopResult.outcome (done/escalate/availability/cost_exceeded)
    classification: str     # the escalation policy's class (done/capability/availability/cost)
    model: str = ""
    source_kind: str = ""
    claim: str = ""
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    dollars: float = 0.0


@dataclass
class RunRecord:
    """The whole escalation of ONE inference request, readable at a glance."""

    run_id: str
    ts: str
    expires_at: str
    ticket_id: str = ""
    domain: str = ""
    task_class: str = ""
    agent_id: str = ""
    urgency: str = ""
    question: str = ""
    resolution: str = RESOLUTION_ERROR   # pessimistic: only a clean exit overwrites this
    answer: str = ""
    hops: list[HopRecord] = field(default_factory=list)
    #: null until analyzed. Once written, never re-derive: {verdict, why, analyzed_at, analyzer}.
    summary: dict | None = None

    @classmethod
    def begin(cls, *, ticket: dict, domain: str, task_class: str, agent_id: str = "",
              urgency: str = "") -> RunRecord:
        now = datetime.now(timezone.utc)
        question = (ticket.get("description") or ticket.get("title") or "")
        return cls(
            run_id=str(uuid.uuid4()),
            ts=now.isoformat(),
            expires_at=(now + timedelta(days=RETENTION_DAYS)).isoformat(),
            ticket_id=str(ticket.get("id", "")),
            domain=domain,
            task_class=task_class,
            agent_id=agent_id,
            urgency=urgency,
            question=question[:QUESTION_EXCERPT_CHARS],
        )

    def add_hop(self, *, hop: int, required_difficulty: str, result, classification: str) -> None:
        """Record one rung. Reads a LoopResult duck-typed so a stub domain needs no real device."""
        self.hops.append(HopRecord(
            hop=hop,
            required_difficulty=required_difficulty,
            outcome=getattr(result, "outcome", ""),
            classification=classification,
            model=getattr(result, "model", "") or "",
            source_kind=getattr(result, "source_kind", "") or "",
            claim=extract_answer(getattr(result, "text", "") or "")[:200],
            turns=getattr(result, "turns", 0) or 0,
            input_tokens=getattr(result, "input_tokens", 0) or 0,
            output_tokens=getattr(result, "output_tokens", 0) or 0,
            dollars=float(getattr(result, "cost_usd", 0.0) or 0.0),
        ))

    @property
    def total_dollars(self) -> float:
        return round(sum(h.dollars for h in self.hops), 6)

    def to_dict(self) -> dict:
        """`summary` first: a reader who already analyzed this run should never scroll past it."""
        return {
            "schema": SCHEMA,
            "summary": self.summary,
            "run_id": self.run_id,
            "ts": self.ts,
            "expires_at": self.expires_at,
            "ticket_id": self.ticket_id,
            "domain": self.domain,
            "task_class": self.task_class,
            "agent_id": self.agent_id,
            "urgency": self.urgency,
            "question": self.question,
            "resolution": self.resolution,
            "answer": self.answer,
            "hops_used": len(self.hops),
            "total_dollars": self.total_dollars,
            "hops": [asdict(h) for h in self.hops],
        }

    def path(self) -> Path:
        day = self.ts[:10].replace("-", "")
        return runs_root() / day / f"run_{self.run_id}.json"

    def write(self) -> Path | None:
        """Persist. Fail-soft: a lost record is bad, a crashed inference run is worse."""
        try:
            p = self.path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str))
            tmp.replace(p)  # atomic: a reader never sees a half-written run
            log.info("run_record: wrote %s (resolution=%s hops=%d)",
                     p, self.resolution, len(self.hops))
            return p
        except Exception as exc:  # noqa: BLE001 — never break a run to save its record
            log.warning("run_record: failed to write run %s: %s", self.run_id, exc)
            return None
