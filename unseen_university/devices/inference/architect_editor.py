"""
architect_editor.py — the two-role coding flow (D-coding-loop-redesign-aider-survey-2026-07-04).

A single small model asked to orient + plan + serialize edits in one ReAct stream never
reaches an edit (2026-07-04 DS.0 observe-runs: 0 Write/Edit attempts across 149 tool calls;
it read-wanders and dies in orientation). aider's architect mode splits the jobs: an
ARCHITECT resolves the task into plain file-change instructions; an EDITOR turns those into
an actual edit. This flow runs ONE attempt as that pair and returns a LoopResult the domain's
escalation walk classifies exactly as it classifies a single-loop attempt — so the walk (the
money-safety) is untouched; only 'what one attempt is' changes.

Roles:
  - ARCHITECT: an AgenticLoop offered Read/Bash but NOT Edit/Write (the constraint is
    STRUCTURAL — the tool is not offered — not a prompt request), with a planner system
    prompt. It emits a PLAN (a done-envelope 'plan'/'result' field, or its final text). It
    cannot edit, so it can only plan.
  - EDITOR (e.g. devstral): an AgenticLoop with the full tool set and an 'apply this plan'
    system prompt; the plan rides in its first message. Its narrow job is to serialize the
    plan into Edit/Write calls. Its LoopResult is the attempt's result.

If the architect does not reach DONE (availability/cost/max-turns/escalate), its LoopResult
is returned unchanged — the walk then re-selects or bumps as it would for any attempt, and no
editor run is wasted on a plan that was never produced.

Tier note (D-coding-loop-redesign): the split's value grows when the architect is a STRONGER
model than the editor. Both roles thread `escalation_hop`, so a capability bump lifts both;
the concrete stronger-planner target on Hex is qwen3-coder:30b (bigger than devstral-24b) —
wiring per-role tier selection is follow-up routing work, not this attempt's mechanics.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from unseen_university.devices.inference.agentic_loop import (
    _REPO_ROOT,
    HISTORY_WINDOW_TURNS,
    LOOP_AVAILABILITY,
    LOOP_DONE,
    LOOP_ESCALATE,
    AgenticLoop,
    LoopResult,
    NativeToolCodec,
)
from unseen_university.devices.inference.block_apply import (
    apply_blocks_to_dir,
    build_repair_message,
    failure_class,
)

log = logging.getLogger(__name__)


def _log_repair_pair(ticket_id: str, turn: int, failure_class_name: str, applied: int) -> None:
    """Log a (failure-class → successful-repair) pair to the io_corpus with role/turn labels.

    Recurring repair shapes are future nexus rows (T-aider-port-reflection-repair-loop). Fail-soft:
    corpus write must never break the editor loop.
    """
    try:
        from unseen_university.devices.inference.io_corpus import capture
        capture({
            "kind": "editor_repair_pair",
            "ticket_id": ticket_id,
            "role": "editor",
            "turn": turn,
            "failure_class": failure_class_name,
            "outcome": "repaired",
            "applied": applied,
        })
    except Exception as exc:  # noqa: BLE001 — corpus write is best-effort
        log.warning("architect_editor: repair-pair corpus write failed for %s: %s", ticket_id, exc)


def _log_verdict(ticket_id: str, verdict, applied: int) -> None:
    """Log the deterministic edit verdict to the io_corpus (the verdict column for the nexus write).

    The rung is recorded exactly as produced — a failure rung on cap-exhaustion is logged as a
    failure, never masked. Fail-soft. (T-aider-port-verdict-gate.)"""
    try:
        from unseen_university.devices.inference.io_corpus import capture
        capture({"kind": "editor_verdict", "ticket_id": ticket_id, "role": "editor",
                 "rung": verdict.rung, "applied": applied, "detail": verdict.detail[:800]})
    except Exception as exc:  # noqa: BLE001
        log.warning("architect_editor: verdict corpus write failed for %s: %s", ticket_id, exc)


#: Injection budget — never feed more than this many chars of named-file content back to the
#: architect (a huge file named would otherwise blow the context; F-D is the failure to avoid).
_MENTION_INJECT_BUDGET = 24_000
_MENTION_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist", ".tox"}


def get_file_mentions(content: str, repo_files) -> set:
    """Return the repo files NAMED in `content` (port of aider base_coder.get_file_mentions).

    Deterministic word/basename match against the ACTUAL repo file list — no fuzzy identity (the
    ticket's design rule). A full relpath present as a word matches; a basename matches only when
    it looks path-like (contains ``/ . _ -``) AND is unique among repo files AND appears verbatim.
    """
    words = {w.rstrip(",.!;:?").strip("\"'`*_").replace("\\", "/") for w in content.split()}
    mentioned = set()
    fname_to_rel: dict = {}
    for rel in repo_files:
        norm = rel.replace("\\", "/")
        if norm in words:
            mentioned.add(rel)
        base = Path(rel).name
        if any(c in base for c in "/\\._-"):
            fname_to_rel.setdefault(base, []).append(rel)
    for base, rels in fname_to_rel.items():
        if len(rels) == 1 and base in words:
            mentioned.add(rels[0])
    return mentioned


def _repo_relative_files(repo_root: Path) -> set:
    """The repo's file set as rel-paths (for mention matching). Skips vcs/venv/build dirs."""
    files = set()
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _MENTION_SKIP_DIRS for part in p.parts):
            continue
        try:
            files.add(str(p.relative_to(repo_root)))
        except ValueError:
            continue
    return files


def _emit_file_set_corpus(ticket_id: str, files: list) -> None:
    """Emit the architect's resolved file-set to the corpus (a nexus-row candidate for the
    accumulating arm: 'which files does this question touch'). Fail-soft."""
    try:
        from unseen_university.devices.inference.io_corpus import capture
        capture({"kind": "architect_file_set", "ticket_id": ticket_id,
                 "role": "architect", "files": files})
    except Exception as exc:  # noqa: BLE001
        log.warning("architect_editor: file-set corpus write failed for %s: %s", ticket_id, exc)


def _build_file_injection(mentions: set, repo_root: Path) -> str:
    """Render named files' full content into an injection block, bounded by _MENTION_INJECT_BUDGET."""
    blocks = []
    used = 0
    for rel in sorted(mentions):
        try:
            body = (repo_root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        block = f"### {rel}\n```\n{body}\n```"
        if used + len(block) > _MENTION_INJECT_BUDGET and blocks:
            break
        blocks.append(block)
        used += len(block)
    if not blocks:
        return ""
    return ("\n\n## Files you named — full content injected below (do NOT Read them again):\n"
            + "\n\n".join(blocks))

#: The architect may inspect the repo but MUST NOT edit — so it is offered read-only tools.
ARCHITECT_TOOLS = ["Read", "Bash"]

ARCHITECT_PROMPT = """\
You are the ARCHITECT. Resolve the coding task into a concrete PLAN of file changes — do
NOT make the changes yourself (you have NO edit tools; a separate editor applies your plan).
Use Read to read the whole files you need (one Read returns the entire file), and Bash only for
grep/ls. Do NOT run the test suite — reading the code is enough to plan; the editor runs tests.

When you have read enough, STOP reading and write the PLAN: a numbered list of edits, each
naming the absolute file path and the exact change (what to find, what to replace it with, or the
full content for a new file). Keep it specific enough that an editor can apply it without
re-deciding anything.

Emit the plan as a done envelope whose `result` field IS the plan, and nothing else:
{"status": "done", "result": "<numbered file-change plan>", "error_class": null, "error_number": null}
If you write the plan as plain text instead, that is still accepted — but do not keep reading or
say you will implement it yourself. Your whole job is to hand over the numbered plan."""

EDITOR_PROMPT = """\
You are the EDITOR. An EDIT PLAN produced by the architect is given in the first message.
Your only job is to APPLY it: for each planned change, call Edit (exact-string replacement)
or Write (whole file), using absolute paths. Do not re-plan or re-explore beyond what you
need to apply a change. After applying the plan, run the tests named in the plan (or the
ticket) and then signal done."""

#: The BLOCK EDITOR prompt — the editor's whole response IS the edits. There is no tool loop
#: and nothing to *choose* to call (fixes F-B: the DS editor never chose to call Edit). The
#: model emits SEARCH/REPLACE blocks; deterministic code (block_apply) applies them through a
#: forgiving ladder (fixes F-C: whitespace/elision drift no longer bounces the edit).
BLOCK_EDITOR_PROMPT = """\
You are the EDITOR. An EDIT PLAN produced by the architect is in the first message. Turn that
plan into edits and RETURN ONLY *SEARCH/REPLACE* blocks — no prose, no tool calls, no commentary.

Every *SEARCH/REPLACE block* uses exactly this format (the filename alone on the line above it):

path/to/file.py
<<<<<<< SEARCH
    exact existing lines to find
=======
    the replacement lines
>>>>>>> REPLACE

Rules:
- The SEARCH section must match the existing file content (indentation, comments, docstrings).
- Each block replaces the FIRST match; use several small blocks rather than one huge one.
- Include enough surrounding lines in SEARCH to match uniquely.
- To create a NEW file, use an empty SEARCH section and put the whole file in REPLACE.
- Use a relative path (as named in the plan) on the line directly above each block.
ONLY EVER RETURN CODE IN A *SEARCH/REPLACE BLOCK*."""


# A finish counts as a plan the editor can act on if it names a file path (…/x.py) or is a
# numbered/bulleted list of steps. This is the min-substance guard that keeps the salvage from
# handing empty prose or a bare "I can't do this" to the editor — those still escalate.
_PLAN_FILE_RE = re.compile(r"[\w./-]+\.\w+")          # a path-ish token with an extension
_PLAN_STEP_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S")  # "1. ", "2) ", "- ", "* "


def _is_substantive_plan(text: str) -> bool:
    """True if `text` looks like a real edit plan (names a file OR has numbered/bulleted steps).

    Deliberately permissive on shape (a weak model's plan is rarely clean JSON) but requires SOME
    structure, so empty/garbage/refusal text is not handed to the editor as if it were a plan.
    """
    if not text or len(text.strip()) < 40:
        return False
    return bool(_PLAN_FILE_RE.search(text) or _PLAN_STEP_RE.search(text))


class ArchitectEditorFlow:
    """One coding attempt as an architect(plan)→editor(apply) pair; returns a LoopResult.

    Drop-in for a single AgenticLoop attempt: same inputs, same LoopResult contract, so the
    domain's escalation walk classifies it identically. The split is the whole behavior change.
    """

    def __init__(
        self,
        *,
        critic_enabled: bool = False,
        inference_device=None,
        history_window_turns: int = HISTORY_WINDOW_TURNS,
        aci_mode: bool = False,
        block_editor_enabled: bool = False,
    ) -> None:
        self._critic_enabled = critic_enabled
        self._inference_device = inference_device
        self._history_window_turns = history_window_turns
        # Minion-tier ACI (windowed Read + edit-centric tools) applies to BOTH roles — the
        # architect reads to plan and the editor reads to apply, both on the weak local tier.
        self._aci_mode = aci_mode
        # When True the EDITOR phase is ONE completion whose response IS SEARCH/REPLACE blocks,
        # applied deterministically (block_apply) — no tool loop, nothing to *choose* to call
        # (fixes F-B/F-C). Default off: the tool-loop editor stays the proven default until this
        # path is proven (T-aider-port-editor-block-contract; ticket rollback = flip the flag).
        self._block_editor_enabled = block_editor_enabled

    def run(
        self,
        *,
        system_prompt: str,
        initial_message: str,
        task_class: str = "worker",
        domain: str = "",
        ticket_id: str = "?",
        agent_id: str = "",
        escalation_hop: int = 0,
        prior_attempt: str = "",
        foreground: bool = False,
        cwd: Path | None = None,
    ) -> LoopResult:
        """Run the architect, then (on a produced plan) the editor. Return the attempt's LoopResult."""
        # 1. ARCHITECT — plan only (no edit tools). Critic is an editor-side concern → off here.
        architect = AgenticLoop(
            codec=NativeToolCodec(),
            critic_enabled=False,
            inference_device=self._inference_device,
            history_window_turns=self._history_window_turns,
            tool_names=ARCHITECT_TOOLS,
            aci_mode=self._aci_mode,
            # Read-only planner: whole-file Read + broad-pytest deflection, so it reads whole
            # files and reaches a plan instead of paging forever (T-architect-read-window-unblock).
            plan_mode=True,
        )
        def _run_architect(msg: str) -> LoopResult:
            return architect.run(
                system_prompt=ARCHITECT_PROMPT + "\n\n" + system_prompt,
                initial_message=msg,
                task_class=task_class,
                domain=domain,
                ticket_id=ticket_id,
                agent_id=agent_id,
                escalation_hop=escalation_hop,
                prior_attempt=prior_attempt,
                foreground=foreground,
                cwd=cwd,
                role="architect",
            )

        plan_result = _run_architect(initial_message)
        plan = self._extract_plan(plan_result)

        # P5: file-mention handshake (T-aider-port-file-mention-handshake). If the architect NAMED
        # repo files, inject their full content and reflect ONCE — so it stops spending turns
        # Reading files it could just name (F-A/F-D). With P1's packet this collapses most
        # orientation to ≤1 reflection. Deterministic word/basename match; capped at one reflection.
        repo_root = Path(cwd) if cwd is not None else _REPO_ROOT
        mentions = get_file_mentions((plan_result.text or "") + "\n" + plan,
                                     _repo_relative_files(repo_root))
        if mentions:
            _emit_file_set_corpus(ticket_id, sorted(mentions))
            injection = _build_file_injection(mentions, repo_root)
            if injection:
                log.info("architect_editor: crossing|step=file-mention-inject|ticket=%s|files=%d "
                         "— one reflection", ticket_id, len(mentions))
                plan_result = _run_architect(initial_message + injection)
                plan = self._extract_plan(plan_result)

        if plan_result.outcome != LOOP_DONE:
            # The architect did not emit a clean done-envelope. But a weak local model routinely
            # produces a REAL plan and then drifts into prose ("Now I'll implement…") or fails to
            # escape its JSON — so json.loads fails, the loop classifies it escalate/max-turns, and
            # the plan is thrown away (observed in the corpus, 2026-07-05). Don't depend on a 24B
            # model emitting escaped JSON: if the finish text is a SUBSTANTIVE plan (names a file
            # path or has numbered steps), accept it and run the editor. Otherwise hand back to the
            # walk unchanged — a garbage/empty finish still escalates, so the walk's re-select and
            # money-safety are untouched.
            if _is_substantive_plan(plan):
                log.info("architect_editor: salvaged a substantive plan from a non-DONE finish "
                         "(%s) for %s — proceeding to editor", plan_result.outcome, ticket_id)
            else:
                log.info("architect_editor: architect did not reach DONE (%s) and produced no "
                         "substantive plan for %s — returning to walk", plan_result.outcome, ticket_id)
                return plan_result
        # Interface crossing (architect → editor handoff): log it.
        log.info("architect_editor: crossing|step=handoff|ticket=%s|plan_chars=%d — handing plan to editor",
                 ticket_id, len(plan))

        # 2. EDITOR — block-contract (one completion → deterministic apply) when enabled, else the
        # proven tool-loop editor. Both return the attempt's LoopResult; the walk is untouched.
        if self._block_editor_enabled:
            return self._run_block_editor(
                plan=plan,
                system_prompt=system_prompt,
                initial_message=initial_message,
                task_class=task_class,
                domain=domain,
                ticket_id=ticket_id,
                agent_id=agent_id,
                escalation_hop=escalation_hop,
                prior_attempt=prior_attempt,
                foreground=foreground,
                cwd=cwd,
            )

        # 2b. EDITOR (tool-loop, default) — apply the plan with the full tool set.
        editor = AgenticLoop(
            codec=NativeToolCodec(),
            critic_enabled=self._critic_enabled,
            inference_device=self._inference_device,
            history_window_turns=self._history_window_turns,
            aci_mode=self._aci_mode,
        )
        editor_message = (
            "## EDIT PLAN (produced by the architect — apply it exactly)\n"
            f"{plan}\n\n"
            "## TICKET (context)\n"
            f"{initial_message}"
        )
        return editor.run(
            system_prompt=EDITOR_PROMPT + "\n\n" + system_prompt,
            initial_message=editor_message,
            task_class=task_class,
            domain=domain,
            ticket_id=ticket_id,
            agent_id=agent_id,
            escalation_hop=escalation_hop,
            prior_attempt=prior_attempt,
            foreground=foreground,
            cwd=cwd,
            role="editor",
        )

    #: reflection cap — the loop's cost meter (T-aider-port-reflection-repair-loop). Do NOT raise:
    #: each reflection is a paid completion, and an unbounded repair loop is exactly the
    #: budget-exhaustion failure (F-D). 3 = one initial attempt + up to 3 file-grounded repairs.
    MAX_REFLECTIONS = 3

    def _run_block_editor(
        self,
        *,
        plan: str,
        system_prompt: str,
        initial_message: str,
        task_class: str,
        domain: str,
        ticket_id: str,
        agent_id: str,
        escalation_hop: int,
        prior_attempt: str,
        foreground: bool,
        cwd: Path | None,
    ) -> LoopResult:
        """EDITOR as a bounded reflection loop of block-contract completions → deterministic apply.

        The completion's whole response IS the edits (SEARCH/REPLACE blocks) — nothing to *choose*
        to call (fixes F-B), and block_apply's forgiving ladder absorbs whitespace/elision drift
        (fixes F-C). On a failed apply we don't die: we build a RICH, file-grounded repair message
        (the actual 'did you mean' lines, a 'REPLACE already present' note, a partial-success
        ledger) and reflect — up to MAX_REFLECTIONS times (F-C/F-E). Every (failure-class →
        successful-repair) pair is logged to the io_corpus (recurring repair shapes → future nexus
        rows). Outcome mapping keeps the walk's contract:
          - ≥1 block applied across the loop → LOOP_DONE. The envelope is marked UNVERIFIED: this
            path does NOT run tests — verdict-gating is P6. A DONE here only stops the walk
            retrying; the aider device still closes shipped-unproven, so nothing false-closes.
          - 0 applied (nothing parsed / every block failed after repairs) → LOOP_ESCALATE.
          - dispatch raised / no live source with nothing yet applied → LOOP_AVAILABILITY.
        """
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        from unseen_university.devices.inference.clone_commit import CloneCommitter
        from unseen_university.devices.inference import verdict_gate

        inference_device = self._inference_device or InferenceDevice()
        editor_cwd = Path(cwd) if cwd is not None else Path.cwd()
        # Commit-per-edit granularity in the throwaway clone (fail-soft outside a git repo).
        committer = CloneCommitter(editor_cwd)
        # Where to find a plan/ticket-named test for the verdict gate (P6).
        verdict_hint = f"{plan}\n{initial_message}"

        messages = [{
            "role": "user",
            "content": (
                "## EDIT PLAN (produced by the architect — turn it into SEARCH/REPLACE blocks)\n"
                f"{plan}\n\n## TICKET (context)\n{initial_message}"
            ),
        }]

        applied_total: list[str] = []
        in_tok = out_tok = 0
        cost = 0.0
        last_text = ""
        last_reason = "no SEARCH/REPLACE blocks emitted"
        pending_failure: str | None = None  # failure class awaiting a repair attempt
        last_verdict = verdict_gate.Verdict(verdict_gate.UNVERIFIED)

        for turn in range(self.MAX_REFLECTIONS + 1):
            req = InferenceRequest(
                messages=messages,
                system=BLOCK_EDITOR_PROMPT + "\n\n" + system_prompt,
                tools=None,  # the response IS the edits — no tool loop
                task_class=task_class,
                domain=domain,
                ticket_id=ticket_id,
                agent_id=agent_id,
                max_tokens=4096,
                temperature=0.0,
                foreground=foreground,
                escalation_hop=escalation_hop if turn == 0 else 0,
                prior_attempt=prior_attempt if turn == 0 else "",
                role="editor",
                turn=turn,
            )
            log.info("architect_editor: crossing|step=block-editor-dispatch|ticket=%s|turn=%d",
                     ticket_id, turn)
            try:
                response = inference_device.dispatch(req)
            except Exception as exc:
                log.error("architect_editor: block-editor dispatch raised for %s turn %d: %s",
                          ticket_id, turn, exc)
                if applied_total:
                    break  # keep the edits already on disk; report them as the attempt's result
                return LoopResult(LOOP_AVAILABILITY, text=str(exc), turns=turn)
            if response.finish_reason == "error" or response.source_kind == "none":
                log.warning("architect_editor: block-editor no live source (finish=%s kind=%s) %s",
                            response.finish_reason, response.source_kind, ticket_id)
                if applied_total:
                    break
                return LoopResult(LOOP_AVAILABILITY, text=response.text or "", turns=turn)

            in_tok += getattr(response, "input_tokens", 0)
            out_tok += getattr(response, "output_tokens", 0)
            cost += getattr(response, "cost_estimate", 0.0)
            last_text = response.text or ""

            result = apply_blocks_to_dir(last_text, editor_cwd, committer=committer)
            log.info("architect_editor: crossing|step=block-editor-apply|ticket=%s|turn=%d|"
                     "applied=%d|failed=%d%s", ticket_id, turn, len(result.applied),
                     len(result.failed), "|parse_error" if result.parse_error else "")

            # A prior round's failure that THIS round applied over = a successful repair → log it.
            if pending_failure is not None and result.applied:
                _log_repair_pair(ticket_id, turn, pending_failure, len(result.applied))
            applied_total.extend(result.applied)

            if result.clean:
                # P6 VERDICT GATE: the edit applied cleanly — now VERIFY it (lint/compile + the
                # plan-named test). A fixable failure (compile/lint error, red test) re-enters the
                # SAME bounded reflection loop (cost stays ≤ cap+1 dispatches across all failure
                # sources). A passing rung, or an un-runnable test, stops. The verdict is DATA —
                # it does NOT flip DONE→escalate; the walk contract is untouched.
                last_verdict, vrepair = verdict_gate.evaluate(editor_cwd, applied_total, verdict_hint)
                log.info("architect_editor: crossing|step=verdict|ticket=%s|turn=%d|rung=%s",
                         ticket_id, turn, last_verdict.rung)
                if vrepair is None or turn == self.MAX_REFLECTIONS:
                    # Verified as far as it got — or out of reflections (last_verdict is HONEST: a
                    # broken edit that never got fixed carries a FAILURE rung, never a passing one).
                    break
                pending_failure = last_verdict.rung
                messages = messages + [
                    {"role": "assistant", "content": last_text},
                    {"role": "user", "content": vrepair},
                ]
                continue

            last_reason = result.parse_error or "; ".join(p for p, *_ in result.failed)
            pending_failure = failure_class(result, editor_cwd)
            repair = build_repair_message(result, editor_cwd)
            if turn == self.MAX_REFLECTIONS or not repair:
                break
            messages = messages + [
                {"role": "assistant", "content": last_text},
                {"role": "user", "content": repair},
            ]

        if applied_total:
            files = ", ".join(dict.fromkeys(applied_total))  # de-dup, preserve order
            _log_verdict(ticket_id, last_verdict, len(applied_total))
            return LoopResult(
                LOOP_DONE,
                text=last_text,
                envelope={
                    "status": "done",
                    "result": f"applied {len(applied_total)} edit(s) to: {files} "
                              f"[verdict: {last_verdict.rung}]",
                    # The verdict column (fingerprint → plan → VERDICT) — DATA for the nexus write,
                    # honest even on cap-exhaustion (a failure rung is never masked as passing).
                    "verdict": last_verdict.as_dict(),
                    "error_class": None,
                    "error_number": None,
                },
                turns=1, input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
            )
        return LoopResult(
            LOOP_ESCALATE,
            text=last_text + f"\n\n[block-editor: 0 edits applied after reflection — {last_reason}]",
            turns=1, input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )

    @staticmethod
    def _extract_plan(result: LoopResult) -> str:
        """Pull the plan text from the architect's DONE result — envelope 'plan'/'result', else text."""
        env = result.envelope or {}
        return (env.get("plan") or env.get("result") or result.text or "").strip()
