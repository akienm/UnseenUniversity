# D-cc-nightly-learning-2026-06-13

**CC's Nightly Learning Loop**

CC processes transcripts and improves the palace while Akien sleeps.

---

## Problem

CC sprints during the day (reactive, task-driven). But CC also needs to:
1. Extract feedback from conversation transcripts
2. Update the memory/palace with learnings
3. Prepare tomorrow's context
4. Improve without asking (autonomous learning)

Currently this is manual. User has to remember to save feedback. Need automated nightly processing.

---

## Solution: CC Nightly Loop

**Time:** 3:30 AM daily (same as learning_pipeline.py)

**Job:** Read today's transcript, extract value, update palace.

```
1. LOAD TODAY'S TRANSCRIPT
   - Session log from ~/.claude/projects/.../f83c0289-*.jsonl
   - Or from Claude Code session history

2. CLASSIFY FEEDBACK
   - ChatClassifier for turns containing feedback signals
   - Look for patterns: "that's good", "stop doing X", "I like when...", "CC++"
   - Extract: who said what, context, type (reinforcement/correction/observation)

3. UPDATE MEMORY
   - Feedback tree: add validated rules/patterns
   - User profile: update goals, preferences, working style
   - Project state: update status, decisions, blockers
   - Reference catalog: new external resources mentioned

4. PREPARE TOMORROW
   - Write session briefing to palace
   - Flag high-priority decisions pending
   - Surface patterns that emerged (e.g., "Akien prefers master control patterns")

5. METRICS
   - Log: feedback items processed, memory updates, errors
   - Surface any conflicts (what CC thought vs what transcript shows)
```

---

## Specific Tasks for CC Nightly

### Task 1: Feedback Extraction
```python
def extract_feedback_from_transcript(session_log):
    """
    Read session transcript. Find:
    - Explicit: "good work", "don't do X", "CC++"
    - Implicit: User approved a design → add to positive patterns
    - Questions: "better ways?" → user thinking through alternatives → save reasoning
    - Outcomes: "that fixed it" → validation of approach
    
    Return: list of (feedback_type, content, context, confidence)
    """
```

Result: Update `memory/*.md` files with new feedback entries.

### Task 2: Pattern Recognition
```python
def extract_patterns_from_transcript(session_log):
    """
    Read transcript. Find recurring patterns:
    - Design patterns user prefers (e.g., "master control", "event sourcing")
    - Problem-solving approaches that worked
    - Mistakes to avoid (touched SQLite → user corrected → learn)
    - Collaboration style insights
    
    Return: patterns with confidence scores
    """
```

Result: Update palace with new design pattern entries, refinement of user profile.

### Task 3: Decision Tracking
```python
def track_decisions_from_transcript(session_log):
    """
    Decisions made today that should be in the palace:
    - T-guru-loop-master-control: approved design
    - D-cc-nightly-learning-2026-06-13: this decision
    - Architecture choices, why, trade-offs considered
    
    Return: decision nodes ready to INSERT into palace
    """
```

Result: Palace gets complete record of today's design decisions.

### Task 4: Context Preparation
```python
def prepare_tomorrow_context(session_log):
    """
    Write briefing for next session:
    - What's in-flight
    - High-priority tickets
    - Decisions pending approval
    - Key insights from today
    - Patterns that emerged
    
    Write to: palace.sessions.<YYYYMMDD>.brief
    """
```

Result: `/context-load` tomorrow finds a prepared briefing, starts faster.

---

## Operational Design

### Trigger
```bash
# In cron_learning_pipeline.py, after the main learning pipeline:
python3 lab/claudecode/cc_nightly_learning.py
```

### Output
```
~/.unseen_university/logs/cc_nightly_learning.log

Sample output:
  feedback_items: 8 extracted
  patterns_found: 3 new, 2 refined
  decisions_logged: 5
  palace_updates: 16 inserts/updates
  errors: 0
```

### Idempotent
- Same transcript run twice = same result
- Safe to run multiple times
- Marked as "processed" after first run

---

## What Gets Updated in Palace

1. **Feedback tree** (`memory_palace.path LIKE 'cc/feedback/%'`)
   - New entries from transcripts
   - Validation (did user confirm?)
   - Confidence scores

2. **User profile** (`palace.shared.akien.*`)
   - Goals (extracted from goals mentioned)
   - Preferences (what approaches Akien likes)
   - Communication style (what signals matter)

3. **Design pattern catalog** (`palace.patterns.*`)
   - Master control pattern (approved today)
   - Event sourcing (used in builder learning loop)
   - Others emerging from this session

4. **Session summary** (`palace.sessions.<date>.brief`)
   - What happened
   - What's next
   - Key learnings

5. **Decision log** (`palace.decisions.*`)
   - T-guru-loop-master-control
   - D-cc-nightly-learning-2026-06-13
   - Others filed during day

---

## Implementation Path

### Phase 1: Extract (this sprint)
- Read transcript
- Find feedback signals
- Write to feedback tree

### Phase 2: Classify (next sprint)
- Use ChatClassifier for transcript analysis
- Separate signal types (reinforcement, correction, observation)
- Confidence scoring

### Phase 3: Update (following sprint)
- Automatic palace updates
- Conflict detection (what CC thought vs reality)
- Session briefing generation

### Phase 4: Autonomous (future)
- Learning affects next sprint (Improver-style)
- Patterns from nightly update ToolLoop
- CC improves based on yesterday's feedback

---

## Benefits

✅ **No manual memory updates** — transcript processing is automatic
✅ **Feedback captured** — even offhand comments become learning
✅ **Tomorrow prepared** — next session starts with briefing
✅ **Pattern accumulation** — over time, design preferences become explicit
✅ **Self-improving** — CC learns what works, applies it next sprint

---

## Example: Today's Session

Transcript contains:
- "good work (that was positive feedback, you have a feedback tree now)"
- "Guru Loop" design approved
- T-guru-loop-master-control filed
- Multiple patterns discussed (master control, supervisor, circuit breaker)

Nightly job extracts:
1. Positive feedback: Guru Loop design validated ✅
2. Patterns: Master control + circuit breaker = preferred resilience pattern
3. Decision: T-guru-loop-master-control (approved in design)
4. User profile: Prefers elegant, minimal core solutions; appreciates disappearing design

Tomorrow's `/context-load` shows:
```
Akien's preferences (from nightly): Master control pattern approved
In-flight: T-guru-loop-master-control (M size, design approved)
Patterns: Master control + circuit breaker are now tagged as approved patterns
```

CC starts tomorrow *already knowing* what Akien values.

---

## Code Structure

```
lab/claudecode/cc_nightly_learning.py
  ├─ load_transcript(session_id)
  ├─ extract_feedback(transcript)
  ├─ extract_patterns(transcript)
  ├─ track_decisions(transcript)
  ├─ prepare_tomorrow(transcript)
  ├─ update_palace(updates)
  └─ log_metrics()
```

Each function is independent, can be tested separately.

---

## Related Decisions

- **T-classifier-device** (already built): provides ChatClassifier for transcript analysis
- **D-adc-phase-5** (ongoing): palace integration, which this feeds into
- **palace.shared.akien** (existing): user profile updates go here
