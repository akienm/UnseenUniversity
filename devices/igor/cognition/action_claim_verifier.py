"""
action_claim_verifier.py — T-igor-emit-action-confabulation.

Igor's response composition can produce confidently-false action claims
('I ticketed it', 'I filed that') without an actual evidence trail. This
module detects those claims at reply-emit time and checks for verification
anchors in the recent past. When a claim is unverified, it logs a
CONFAB_CAUGHT ring entry and pushes a high-salience TWM marker so the
NEXT turn picks up the warning and Igor can self-correct.

Two-phase approach:
  Phase 1 (detection): DETECT action-claim phrases, LOOK for evidence
    anchors, LOG + TWM marker if unverified.
  Phase 2 (T-active-suppression-action-claims): SUPPRESS unverified
    claims by stripping them from response text before user sees it.
    TWM marker still fires so Igor self-corrects on next turn.

Biomimetic framing: this is the equivalent of reality-monitoring in
metacognition. Healthy minds catch the difference between 'I remember
doing X' and 'I imagined doing X' before reporting it. Igor's failure
mode is reporting the imagined as the remembered. The watcher catches
the slip after-the-fact and lets self-correction happen on the next
turn — same way a person might say 'I sent that email' and then,
checking, discover they never hit send.
"""

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Action-claim patterns ────────────────────────────────────────────────────

# Narrow on purpose. False positives are noise; the goal is to catch the
# specific class of confabulation Akien observed in the 2026-04-13 transcript:
# "I ticketed it" / "the ticket is already in the database" when no write
# actually happened. Future patterns can be added once we observe more
# failure modes in the log.
_ACTION_CLAIM_PATTERNS = [
    # Ticket filing claims — the exact class from 2026-04-13
    r"\bi(?:'ve| have)? (?:just )?ticketed (?:it|that|this)\b",
    r"\bi(?:'ve| have)? (?:just )?filed (?:it|that|this|a ticket|the ticket)\b",
    # Allow up to 80 chars between 'the ticket' and 'is/has been' (no period
    # in between) so subordinate clauses don't break the match — e.g.
    # 'the ticket about the privacy-guard halt is already in the database'
    r"\bthe ticket\b[^.]{0,80}?\b(?:is|has been) (?:already )?(?:in|filed|added|created|written)\b",
    r"\bi (?:added|wrote|created|filed) (?:it|that|this) (?:to |as |into |in )(?:the )?(?:ticket|queue|list|database|store)\b",
    r"\bi(?:'ve| have)? (?:just )?(?:added|written|created|saved|stored|recorded) (?:it|that|this) (?:to|in|into) (?:the )?(?:queue|database|store|registry|memory|notes)\b",
    r"\bnoted\.? (?:that(?:'s| is)? )?(?:in|to|added)\b",
    r"\bi(?:'ve| have)? (?:just )?(?:committed|pushed|saved) (?:it|that|this)\b",
    r"\bi(?:'ve| have)? (?:just )?recorded (?:it|that|this)\b",
]

_ACTION_CLAIM_RE = re.compile("|".join(_ACTION_CLAIM_PATTERNS), re.IGNORECASE)


def detect_action_claims(text: str) -> list[str]:
    """Return the matched action-claim phrases found in text. Empty list
    if none match. Case-insensitive. Returns the actual matched substrings,
    not the patterns, so the log shows what Igor actually said."""
    if not text or not isinstance(text, str):
        return []
    return [m.group(0) for m in _ACTION_CLAIM_RE.finditer(text)]


# ── Evidence lookup ──────────────────────────────────────────────────────────

# How far back to look for evidence anchors. Action claims are about something
# Igor "just did" — so the relevant window is the past few minutes, not hours.
_EVIDENCE_WINDOW_SEC = 120

# Refractory window: if ring_memory already has a confab_caught entry for this
# thread within the last N entries, suppress the redundant TWM push.
_REFRACTORY_TURNS = int(os.getenv("IGOR_CONFAB_REFRACTORY_TURNS", "3"))


def _cc_queue_recently_modified(window_sec: int = _EVIDENCE_WINDOW_SEC) -> bool:
    """Check if any ticket was saved to Postgres within the window.

    Replaces queue.json mtime check (T-cc-queue-drop-json-stage-b).
    Queries MAX(updated_at) from clan.memories WHERE parent_id='TICKETS_ROOT'.
    """
    try:
        from lab.claudecode.cc_queue import _db_conn, TICKETS_ROOT_ID

        conn = _db_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT MAX(updated_at) FROM clan.memories WHERE parent_id = %s",
                (TICKETS_ROOT_ID,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return False
        from datetime import datetime, timezone

        latest = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - latest).total_seconds()
        return age <= window_sec
    except Exception:
        return False


def _recent_tool_results(cortex, window_sec: int = _EVIDENCE_WINDOW_SEC) -> list:
    """Return recent ring entries that look like tool-execution evidence:
    RESOLVED|<tool>|... and TOOL_RESULT|<tool>|... markers from the past
    window. Best-effort — empty list on any error."""
    try:
        results = []
        # search_ring is the existing helper the cortex has for this
        if hasattr(cortex, "search_ring"):
            for term in ("RESOLVED|", "TOOL_RESULT|"):
                hits = cortex.search_ring([term], limit=20)
                for h in hits:
                    ts_str = h.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        age = (datetime.now() - ts.replace(tzinfo=None)).total_seconds()
                        if age <= window_sec:
                            results.append(h)
                    except Exception as _exc:
                        from .forensic_logger import log_error as _le

                        _le(
                            kind="SILENT_EXCEPT",
                            detail=f"action_claim_verifier.py:109: {_exc}",
                        )
        return results
    except Exception:
        return []


def find_evidence(cortex, window_sec: int = _EVIDENCE_WINDOW_SEC) -> dict:
    """Look for evidence anchors in the recent past. Returns a dict with
    boolean flags + a count of recent tool results.

    A claim is considered VERIFIED if any of:
      - cc_queue.json was modified within the window (ticket-filing path)
      - at least one RESOLVED|/TOOL_RESULT| ring entry exists in the window
        (general tool-execution path)

    This is intentionally generous in the first pass — false negatives
    (catching real claims as confabulations) are worse than false positives
    (letting through some real confabulations). We loosen as we observe.
    """
    queue_modified = _cc_queue_recently_modified(window_sec)
    tool_results = _recent_tool_results(cortex, window_sec)
    return {
        "queue_modified": queue_modified,
        "tool_results_count": len(tool_results),
        "any_evidence": queue_modified or len(tool_results) > 0,
    }


# ── Forensic log ─────────────────────────────────────────────────────────────


def _confab_log(stage: str, **fields) -> None:
    """Forensic log for action-claim verification. Never raises."""
    try:
        from ..paths import paths as _paths

        line = f"{datetime.now().isoformat(timespec='milliseconds')} {stage}"
        for k, v in fields.items():
            line += f" {k}={str(v)[:200].replace(chr(10), ' ')}"
        log_path = _paths().logs / "action_claim_verifier.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception as _exc:
        from .forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"action_claim_verifier.py:153: {_exc}")


# ── Main check ───────────────────────────────────────────────────────────────


def check_response(
    cortex,
    response_text: str,
    turn_id: str = "",
    thread_id: Optional[str] = None,
) -> list[str]:
    """Run the verification pass on a single outgoing reply.

    Returns the list of unverified claims found (empty if none or all
    verified). Side effects when unverified claims are present:
      1. CONFAB_CAUGHT ring entry written
      2. NARRATIVE_GAP TWM observation pushed at high salience so the
         next turn's reasoning has it as dominant context
      3. Forensic log entry

    NEVER raises. Returns unverified claims for caller to act on.
    """
    if not response_text:
        return []

    claims = detect_action_claims(response_text)
    if not claims:
        _confab_log("scan", turn_id=turn_id, claims=0, status="clean")
        return []

    evidence = find_evidence(cortex)
    if evidence["any_evidence"]:
        _confab_log(
            "scan",
            turn_id=turn_id,
            claims=len(claims),
            status="verified",
            queue_modified=evidence["queue_modified"],
            tool_results=evidence["tool_results_count"],
        )
        return []

    # Unverified — record + warn the next turn
    _confab_log(
        "caught",
        turn_id=turn_id,
        thread_id=thread_id or "",
        claims_count=len(claims),
        first_claim=claims[0][:120],
    )

    # Refractory window (T-cc-walk-14): if ring_memory already shows a recent
    # confab_caught for this thread, the claim is likely stale — skip the
    # redundant TWM push (still logged above).
    if cortex is not None and thread_id:
        try:
            recent = cortex.read_ring_memory(
                limit=_REFRACTORY_TURNS,
                category="confab_caught",
                thread_id=thread_id,
            )
            if recent:
                _confab_log(
                    "refractory_suppressed",
                    turn_id=turn_id,
                    thread_id=thread_id,
                    recent_entries=len(recent),
                )
                return claims
        except Exception as _exc:
            from .forensic_logger import log_error as _le

            _le(
                kind="SILENT_EXCEPT",
                detail=f"action_claim_verifier.py refractory check: {_exc}",
            )

    try:
        cortex.write_ring(
            f"CONFAB_CAUGHT|turn={turn_id}|claim={claims[0][:120]}|"
            f"text={response_text[:200]}",
            category="confab_caught",
            thread_id=thread_id or None,
        )
    except Exception as _exc:
        from .forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"action_claim_verifier.py:213: {_exc}")

    try:
        cortex.twm_push(
            source="action_claim_verifier",
            content_csb=(
                f"CONFAB_CAUGHT|turn={turn_id}|"
                f"unverified_claim={claims[0][:120]}|"
                f"check this claim before continuing — no evidence anchor found"
            ),
            salience=0.92,
            urgency=0.85,
            ttl_seconds=600,
            category="confab_caught",
            thread_id=thread_id or None,
            metadata={
                "turn_id": turn_id,
                "claims": claims,
                "evidence_window_sec": _EVIDENCE_WINDOW_SEC,
            },
        )
    except Exception as _exc:
        from .forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"action_claim_verifier.py:235: {_exc}")

    return claims


def suppress_false_claims(response_text: str, claims: list[str]) -> str:
    """Strip unverified action claims from response text.

    T-active-suppression-action-claims: graduates from detection-only to
    active inline suppression. Removes the false claim phrases and cleans
    up any resulting awkward whitespace. Does NOT add a self-correction
    note — the TWM marker already ensures Igor self-corrects next turn.

    Returns the cleaned text. If all content would be removed, returns
    the original (suppression should never produce an empty reply).
    """
    if not claims or not response_text:
        return response_text

    cleaned = response_text
    for claim in claims:
        # Remove the claim phrase — case-insensitive
        cleaned = re.sub(re.escape(claim), "", cleaned, flags=re.IGNORECASE)

    # Clean up artifacts: double spaces, leading/trailing whitespace, orphaned punctuation
    cleaned = re.sub(r"  +", " ", cleaned)
    cleaned = re.sub(r"^\s*[.,;:!]\s*", "", cleaned)  # leading orphaned punctuation
    cleaned = re.sub(r"\s+([.,;:!])", r"\1", cleaned)  # space before punctuation
    cleaned = cleaned.strip()

    # Safety: never return empty — better to leave the false claim than say nothing
    if not cleaned:
        return response_text

    _confab_log(
        "suppressed",
        claims_removed=len(claims),
        original_len=len(response_text),
        cleaned_len=len(cleaned),
    )
    return cleaned
