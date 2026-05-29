# T-compiled-inference-scraps — Discovery Doc

**Ticket:** T-compiled-inference-scraps  
**Date:** 2026-05-28  
**Status:** In progress — discovery complete; implementation pending pre-approval

## Gate check
T-log-parsing-out-of-igor: **CLOSED** ✓ (verified 2026-05-28)

## Candidates (ranked by ROI)

### 1. `purpose_annotator._annotate_one` — HIGHEST ROI
**File:** `devices/igor/memory/purpose_annotator.py:28`  
**Current behavior:** Calls `call_inner_cc_long` with Haiku for every unannotated memory. Prompt asks for one of 8 fixed categories + a purpose sentence.  
**Compilation target:** 8-category classifier (`skill | fact | preference | constraint | decision | experience | procedure | observation`) is a pure classification problem. Keyword heuristics handle the majority of cases; LLM call only when heuristics are ambiguous.  
**Why highest ROI:** Runs on every unannotated memory (batch_size=2 per cycle); this fires frequently. Classification is deterministic enough that rules can cover 70-80% of cases.  
**Inertia:** MEDIUM (`memory/` but not `memory/models.py`) — no pre-approval required, but document the touch.  
**Scraps pattern:** New file `devices/scraps/purpose_classifier.py` with `classify_purpose(narrative, memory_type) → (category, confidence)`. Returns `(category, HIGH)` for clear rule matches; `(None, LOW)` when ambiguous → caller then invokes LLM.

### 2. `_check_task_completion_semantic` — HIGH ROI
**File:** `devices/igor/main.py:2073`  
**Current behavior:** Gated by `IGOR_TASK_COMPLETION_SEMANTIC` env var. YES/NO question to cheap model; max_tokens=5, temp=0.0. Very low token count but still hits the API.  
**Compilation target:** Pattern-match `response_text` against task_goals using keyword overlap + simple heuristics. If high overlap → YES without API call. If negative phrases → NO without API call. Only call LLM for genuinely ambiguous cases.  
**Inertia:** MEDIUM (`main.py` is large but the function is isolated) — needs pre-approval per safeguards check.  
**Scraps pattern:** New file `devices/scraps/task_completion_check.py`.

### 3. `_try_habit_tiebreaker` — MEDIUM ROI  
**File:** `devices/igor/main.py:1988`  
**Current behavior:** Gated by `IGOR_HABIT_TIEBREAKER` env var. Sends `near_misses` + `user_input` to gpt-4o-mini asking `HABIT:<id>` or `REASON`; max_tokens=20, temp=0.0.  
**Compilation target:** If one habit's trigger word list has strong overlap with user_input but others don't → deterministic winner, no LLM. Only invoke LLM when overlap scores are within threshold.  
**Inertia:** MEDIUM (same `main.py`; same pre-approval needed).

### 4. `validate_against_core` — LOWER ROI (deferred)
**File:** `devices/igor/brainstem/core_patterns.py:768`  
**Current behavior:** Sends response to OpenRouter Haiku for ethics gate (CP1-CP6). Already has `fast_identity_check` (line 878) using `_IDENTITY_THREAT_RULES` for obvious cases.  
**Why deferred:** `brainstem/` is HIGH inertia (explicit in safeguards). `fast_identity_check` already handles the obvious cases. Marginal gain from extending rules vs. the inertia cost. Revisit when the other three are shipping.  
**Pre-approval needed:** YES — brainstem/ is HIGH inertia.

## Not a compilation candidate
`_grade_ne_output` (coa.py:505) — NE quality grader rates three dimensions with genuine judgment. No deterministic rules can replicate the quality assessment reliably.

## Implementation plan

### Phase 1: New Scraps scripts (no pre-approval needed — new files)
1. `devices/scraps/purpose_classifier.py` — keyword-based 8-category classifier
2. `devices/scraps/task_completion_check.py` — overlap-based YES/NO  
3. `devices/scraps/habit_tiebreaker.py` — overlap-score based habit selection

### Phase 2: Caller updates (pre-approval needed for main.py touches)
1. `devices/igor/memory/purpose_annotator.py` — call `purpose_classifier` first; LLM only on LOW confidence
2. `devices/igor/main.py:2073` (_check_task_completion_semantic) — call script first; LLM only if ambiguous
3. `devices/igor/main.py:1988` (_try_habit_tiebreaker) — call script first; LLM only if ambiguous

### Phase 3: Verification
- Unit tests for each Scraps script
- Compare token counts before/after via `infra.llm_calls` table (completion criteria)

## Inertia summary
| File | Inertia | Pre-approval needed |
|---|---|---|
| `devices/scraps/purpose_classifier.py` (new) | LOW | No |
| `devices/scraps/task_completion_check.py` (new) | LOW | No |
| `devices/scraps/habit_tiebreaker.py` (new) | LOW | No |
| `devices/igor/memory/purpose_annotator.py` | MEDIUM | Yes (document) |
| `devices/igor/main.py` | MEDIUM | Yes (surface before coding) |
| `devices/igor/brainstem/core_patterns.py` | HIGH | Yes (inline Akien pre-approval) |
