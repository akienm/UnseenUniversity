# D-six-prompt-patterns-base-class-2026-06-13

**title:** Six core LLM prompt reliability patterns — canonical home in BaseDevice/PromptBuilder
**date:** 2026-06-13
**status:** open
**spawned_tickets:** T-six-patterns-base-class

## Decision narrative

DickSimnel's SYSTEM_RULES (toolloop.py) and SYSTEM_PROMPT (device.py) encode six patterns
that proved necessary for OR/Anthropic models to follow instructions reliably. These are not
DS-specific — any device spawning LLM sub-agents needs all six. Today they are scattered and
undiscoverable. This decision names them, locates them, and proposes a canonical home.

---

## The six patterns

### 1. Exit protocol at the top
The terminal response format ("when done, output {status: done, result: ...}") must appear
at the START of the system prompt, not buried in rules. Models front-load attention; if the
exit protocol is in rule #8, models forget it and produce prose instead of a valid envelope.

**Current location:** `devices/dicksimnel/toolloop.py` `SYSTEM_RULES` (first section: `## Exit protocol`).
**Absent in:** Granny's `_JUDGE_SYSTEM`, Evaluator — those models use natural-language responses,
so exit-protocol is not required. Only required for tool-loop workers.

### 2. Temperature = 0 declared explicitly
Determinism is not implied by the task description. Every inference request that must be
reproducible must declare `temperature=0.0` explicitly. The device that calls inference (not
the inference device itself) must set this — inference proxies do not enforce a default.

**Current location:** `devices/dicksimnel/toolloop.py` line 182 (`temperature=0.0` in `InferenceRequest`).
Also: `devices/granny/daemon.py` line 243, 469 (where Granny calls LLM for dispatch decisions).
**Absent in:** Any device that omits temperature from InferenceRequest defaults to the provider default (often 1.0).

### 3. Consequence framing
Instructions must name what happens if violated: "any other text is an error" or "triggers
re-dispatch." Models that see consequence framing have a lower false-positive rate for
ignoring rules. A rule without consequence is advisory; with consequence it is a constraint.

**Current location:** `devices/dicksimnel/device.py` SYSTEM_PROMPT: "If scope is unclear or touches
HIGH-inertia files: output ESCALATE: <reason>." `toolloop.py` SYSTEM_RULES: "No other non-tool
responses are valid."

### 4. Imperative register
"Output X and stop" — not "you may output X" or "feel free to return X." Permissive language
("you may", "can", "feel free") is read as optional; imperative ("must", "output X", "your first
action must be") is read as required. Every rule in a system prompt should be in imperative register.

**Current location:** `devices/dicksimnel/device.py`: "Your FIRST ACTION must be a tool call."
`devices/dicksimnel/toolloop.py` SYSTEM_RULES: "Run tests before commit" (imperative).

### 5. Tool-first enforcement
On turn 1, `tool_choice: "required"` forces a tool call instead of prose. Models in
planning mode emit several paragraphs describing what they will do before acting — this wastes
tokens, burns turn budget, and produces confusing logs. `tool_choice: "required"` on turn 1
prevents planning-mode narration.

**Current location:** `devices/dicksimnel/toolloop.py` line 172:
`extra = {"tool_choice": "required"} if turn == 0 else {}`
**Currently unique to DickSimnel** — only DS uses a tool loop. Other devices call LLMs for
single-response judgment (Granny, Evaluator) so tool_choice is irrelevant to them.

### 6. Failure-mode naming
System prompts must name specific error class names (ESCALATE, COST_EXCEEDED, TIMEOUT)
rather than generic "if something goes wrong..." phrasing. Named error classes appear
verbatim in model output, enabling downstream parsing without regex fragility.

**Current location:** `devices/dicksimnel/device.py` SYSTEM_PROMPT: "ESCALATE: <reason>".
`devices/dicksimnel/toolloop.py`: DONE_PREFIX, ESCALATE detection in `_parse_terminal_envelope`.

---

## Where these patterns should live

### What belongs in BaseDevice

Not all six belong in BaseDevice — only those that apply to all devices. Analyzing:

| Pattern | All-devices? | Only tool-loop? | Proposed home |
|---|---|---|---|
| 1. Exit protocol | No — only tool-loop workers | Yes | `unseen_university/prompt_builder.py` (new, optional mixin) |
| 2. Temperature = 0 | Yes — any device calling inference | No | `BaseDevice.build_inference_request()` helper (new) |
| 3. Consequence framing | Yes — code review, doc convention | No | Documented in palace; `BaseDevice` docstring |
| 4. Imperative register | Yes — code review, doc convention | No | Documented in palace; prompt linting in audit |
| 5. Tool-first enforcement | No — only tool-loop workers | Yes | `unseen_university/prompt_builder.py` (new, optional mixin) |
| 6. Failure-mode naming | Yes — any device with LLM judgment | No | `unseen_university/prompt_builder.py` constants |

### Proposed: `unseen_university/prompt_builder.py`

A new module with:

```python
# Canonical exit protocol envelope (copy-paste into tool-loop SYSTEM_RULES)
EXIT_PROTOCOL = """
## Exit protocol

When finished, respond ONLY with:
{"status": "done", "result": "<one-line summary>", "error_class": null, "error_number": null}

When escalating, respond ONLY with:
{"status": "escalate", "result": "<reason>", "error_class": "ESCALATE", "error_number": null}

No other non-tool responses are valid. Any other text is an error.
""".strip()

# Named failure classes (import and use in system prompts for parseability)
class FailureClass:
    ESCALATE = "ESCALATE"
    COST_EXCEEDED = "COST_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"


def build_inference_request(*, model, messages, system, tools=None, **kwargs):
    """Build an InferenceRequest with temperature=0 as the default.
    
    Callers can override but must be explicit:
        build_inference_request(..., temperature=1.0)  # creative task, intentional
    """
    from devices.inference.device import InferenceRequest
    kwargs.setdefault("temperature", 0.0)
    return InferenceRequest(model=model, messages=messages, system=system, tools=tools, **kwargs)
```

### What BaseDevice gets

BaseDevice does NOT get the exit protocol or tool-first enforcement directly — those are
tool-loop concepts and BaseDevice has no tool-loop lifecycle. What BaseDevice gets:

1. A reference in its docstring to `prompt_builder.py` for devices that spawn LLM agents.
2. `build_inference_request()` as an inherited helper method (thin wrapper around the module fn).

### Audit surface

Pattern 3 (consequence framing) and 4 (imperative register) are hard to enforce mechanically —
they're stylistic. The right home for enforcement is the prompt audit skill (`/audit-precode`),
not runtime code. The patterns are documented in the palace; the audit skill checks for them.

---

## What this does NOT do

- Does NOT retrofit existing devices in this ticket.
- Does NOT add a mandatory abstract method to BaseDevice for "system_prompt" — that belongs
  only to devices that spawn LLM agents, which is not all devices.
- Does NOT change DickSimnel's SYSTEM_RULES — it already implements all six correctly.

---

## Implementable without breaking existing devices?

Yes. `prompt_builder.py` is a new optional module. Existing devices import it when they want
to adopt the patterns; the base class gets a convenience method that's usable but not required.
No existing device breaks because nothing new is abstract.
