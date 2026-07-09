"""
stuck_ladder.py — the cost-ordered exit for a harvest_mode wall (builder starve-curve).

T-ds-stuck-ladder-and-rung-log / D-ds-harvest-mode-coding-loop-2026-07-05. Once escalation is
off (harvest_mode), a stuck builder has no structured exit — it just hits the turn cap. The
ladder gives it one: on a wall, pick the CHEAPEST viable rung and RECORD which rung was taken.
The distribution over rungs across many stuck events IS the builder starve-curve — how often
the cheap compiled paths sufficed vs how often the expensive last resort (a human/CC) was spent.

Four cost-ordered rungs, tried in order; the first viable one wins:

  1. ANSWER   — consult already-compiled artifacts (build-packet / prereg / corpus) for a known
                answer. Free, and the win. DATA-STARVED TODAY: by the time a run reaches this
                wall the compiled/warm path upstream (dispatch pattern-intercept) already failed
                to serve it, so ``answer_source`` honestly returns None. The real rung-1
                (name the defeating question → look it up → inject + resume) needs the classifier
                and the resume loop — the next two tickets in this chain. So rung 1 is wired as a
                control path with a data-starved source, an honest zero (cf. warm-count=0), NOT a
                fabricated win. Its terminal is None: a bare answer string has no ``DONE:`` prefix
                and would fail the worker_listener completion gate → escalate, collapsing rung 1
                into rung 4. Injecting the answer and RESUMING is T-ds-resume-with-answer-loop.
  2. DROP-TICKET — file the defeating question, mark the current ticket needs-design, move on.
                Cheap, async. STUB HERE: deciding whether a stuck is a nameable design question
                (design-stuck) vs a capability ceiling is the classifier in the next ticket
                (T-ds-defeating-question-classifier); ``drop_classifier`` returns False until then.
  3. HALT     — the device HALT primitive (built, unused today). Blocks. Actuation SEAM here
                (``halt_hook``); the device-side binding (BaseDevice.halt) waits for the driver
                ticket. ``halt_hook=None`` means HALT is unavailable → fall through.
  4. CALL-CC  — sync escalation to CC. The expensive last resort: it spends the exact resource
                the whole program starves (a mind). Its FREQUENCY is the starved-resource metric
                — count of ``rung == 'call_cc'`` records. High now, should fall as the tree fills.

Scope (this ticket): the controller + rungs 1/3/4 + the one-record-per-stuck-event log. Rung 2's
classifier + real drop is the next ticket. Device-side HALT/CALL-CC binding is deferred; the
hooks are the testable seam.

The rung-choice record mirrors ``prereg.py``: append-only JSONL under the canonical memory store
(``<memory_root>/inference_starve``, ``UU_MEMORY_ROOT`` override), fail-soft, an explicit
``domain`` so the same starve-curve instrument generalizes past coding without a schema change.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from unseen_university.memory_root import memory_root

log = logging.getLogger(__name__)

SCHEMA = "inference.stuck_rung.v1"
DEFAULT_DOMAIN = "coding"

# The four cost-ordered rungs (cheapest first).
RUNG_ANSWER = "answer"
RUNG_DROP_TICKET = "drop_ticket"
RUNG_HALT = "halt"
RUNG_CALL_CC = "call_cc"


def starve_root(root: Optional[Path] = None) -> Path:
    """The rung-choice record directory. Explicit ``root`` wins (tests); else the canonical
    memory store at ``<memory_root>/inference_starve`` (respects ``UU_MEMORY_ROOT``)."""
    return Path(root) if root is not None else memory_root() / "inference_starve"


def _starve_file(now: datetime, root: Optional[Path] = None) -> Path:
    return starve_root(root) / f"{now.strftime('%Y%m%d')}.rungchoice.jsonl"


@dataclass
class StuckEvent:
    """One builder-stuck event at a harvest_mode wall — the ladder's input."""

    ticket_id: str
    tier: str          # the FIXED difficulty tier at the wall (harvest mode never escalated)
    turn_reached: int  # how many loop turns ran before the wall (LoopResult.turns)
    domain: str = DEFAULT_DOMAIN


@dataclass
class RungChoice:
    """The rung the ladder took for one stuck event — the record's payload."""

    ticket_id: str
    rung: str
    tier: str
    turn_reached: int
    reason: str


def record_rung_choice(
    choice: RungChoice, *, domain: str = DEFAULT_DOMAIN, root: Optional[Path] = None
) -> Optional[str]:
    """Append one rung-choice record. Returns the path, or None on a swallowed write error.

    Fail-soft (mirrors ``prereg.record_prediction``): a lost starve-curve datum is bad, a stuck
    builder that also crashes on its own bookkeeping is worse.
    """
    try:
        now = datetime.now(timezone.utc)
        record = {
            "schema": SCHEMA,
            "ts": now.isoformat(),
            "id": str(uuid.uuid4()),
            "domain": domain,
            "ticket_id": choice.ticket_id,
            "rung": choice.rung,
            "tier": choice.tier,
            "turn_reached": choice.turn_reached,
            "reason": choice.reason,
        }
        path = _starve_file(now, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return str(path)
    except Exception as exc:  # noqa: BLE001 — bookkeeping must never crash a stuck builder
        log.warning("StuckLadder: failed to record rung choice for %s (non-fatal): %s",
                    choice.ticket_id, exc)
        return None


def read_rung_choices(
    ticket_id: Optional[str] = None, *, root: Optional[Path] = None
) -> "list[dict]":
    """Read rung-choice records (optionally filtered by ticket). The starve-curve is computed
    from these — e.g. call-CC frequency = count where ``rung == RUNG_CALL_CC``."""
    out: list[dict] = []
    d = starve_root(root)
    if not d.exists():
        return out
    for f in sorted(d.glob("*.rungchoice.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception as exc:
                log.warning("StuckLadder: unreadable record in %s: %s", f.name, exc)
                continue
            if ticket_id is None or rec.get("ticket_id") == ticket_id:
                out.append(rec)
    return out


class StuckLadder:
    """Cost-ordered exit for a harvest_mode wall: pick the cheapest viable rung, record it.

    Pure decide + record: the ladder chooses a rung, actuates that rung's injected hook (a
    testable seam), and emits exactly ONE rung-choice record per ``resolve``. The device-side
    binding of HALT / CALL-CC is deferred to the driver ticket; here the hooks default to a
    log-only no-op so the seam is real and assertable without a live device.

    Every rung is an injectable probe so the ladder is hermetically testable and so the next
    tickets can drop in the real classifier (rung 2) and answer source (rung 1) without touching
    the controller:
      * ``answer_source(event) -> str | None`` — rung 1; None today (data-starved, see module doc).
      * ``drop_classifier(event) -> bool``      — rung 2; False until the classifier ticket.
      * ``halt_hook(event) -> None`` or None    — rung 3; None ⇒ HALT unavailable (fall through).
      * ``call_cc_hook(event) -> None``         — rung 4; the last-resort actuation seam.
    """

    def __init__(
        self,
        *,
        answer_source: Optional[Callable[[StuckEvent], Optional[str]]] = None,
        drop_classifier: Optional[Callable[[StuckEvent], bool]] = None,
        halt_hook: Optional[Callable[[StuckEvent], None]] = None,
        call_cc_hook: Optional[Callable[[StuckEvent], None]] = None,
        recorder: Optional[Callable[..., Optional[str]]] = None,
        root: Optional[Path] = None,
    ) -> None:
        self._answer_source = answer_source or (lambda ev: None)
        self._drop_classifier = drop_classifier or (lambda ev: False)
        self._halt_hook = halt_hook  # None ⇒ HALT primitive not wired for this caller
        self._call_cc_hook = call_cc_hook or (lambda ev: None)
        self._recorder = recorder or record_rung_choice
        self._root = root

    def resolve(self, event: StuckEvent) -> RungChoice:
        """Walk the rungs cheapest-first, actuate + record the first viable one, return it."""
        # Rung 1 — ANSWER (free). Data-starved today: answer_source returns None (see module doc).
        answer = self._answer_source(event)
        if answer is not None:
            return self._choose(event, RUNG_ANSWER, "compiled answer available")

        # Rung 2 — DROP-TICKET (cheap, async). Stub: classifier is the next ticket.
        if self._drop_classifier(event):
            return self._choose(event, RUNG_DROP_TICKET, "design-stuck: nameable defeating question")

        # Rung 3 — HALT (blocks). Viable only when the caller wired the HALT seam.
        if self._halt_hook is not None:
            self._halt_hook(event)
            return self._choose(event, RUNG_HALT, "halt primitive available")

        # Rung 4 — CALL-CC (expensive, sync). The last resort; its frequency is the metric.
        self._call_cc_hook(event)
        return self._choose(event, RUNG_CALL_CC, "no cheaper rung viable — sync escalate to CC")

    def _choose(self, event: StuckEvent, rung: str, reason: str) -> RungChoice:
        """Build the RungChoice, log the crossing, and emit exactly one record."""
        choice = RungChoice(
            ticket_id=event.ticket_id, rung=rung, tier=event.tier,
            turn_reached=event.turn_reached, reason=reason,
        )
        # State change + interface crossing: one INFO line per stuck event.
        log.info("StuckLadder: ticket=%s rung=%s tier=%s turn=%d reason=%s",
                 event.ticket_id, rung, event.tier, event.turn_reached, reason)
        self._recorder(choice, domain=event.domain, root=self._root)
        return choice
