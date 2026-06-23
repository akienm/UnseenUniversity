# D-system-alarms-and-tier-requests-2026-06-23
**title:** Requests become tiers; the proxy gets a mouth — a general system_alarms primitive + tier-based inference requests
**date:** 2026-06-23
**status:** open
**spawned_tickets:** T-system-alarms-primitive, T-system-alarms-notify, T-uu-alarms-cli, T-inference-resolve-requests-by-tier, T-inference-empty-response-fallthrough, T-callers-request-tiers, T-inference-specific-model-alarm, T-consequence-system-alarms-tier-requests

## Hypothesis
After this ships, inference callers request a capability TIER (not a specific model); a specific-model request outside a testing context drops a deduped system_alarm naming the callers; failures surface as unignorable system alarms (`uu alarms`) instead of silent logs; and Igor talks again via tier-routed dispatch.

## Measurement Signal
`uu alarms` lists open alarms with per-caller breakdowns; all-providers-down drops a `no-provider:<tier>` alarm + one notification; a non-test caller naming a model appears in `uu alarms` as `specific-model:<model>` with its caller + count; a live Igor chat round-trip returns text; closed alarms appear in `~/.unseen_university/operations/system_alarms/archive/`; the specific-model alarm count trends toward zero as callers convert.

## Goal Link
none: serves the observability north star (unignorable failure surfacing), CP1 (silence is a lie — fail loud + honest), and the inference intentions (proxy-only, tier-based, models-evolve-so-don't-pin-them). Supersedes the built parts of D-inference-provider-router-redesign-2026-06-23 and re-points its reliability layer here.

## Decision narrative
Two ideas converged into one decision, both rooted in the same disease — **silent failure found by accident** (inference dead for days; surfaced only when we went looking; Igor mute the whole time).

**1. The proxy gets a mouth — a general `system_alarms` primitive.** Akien's father was a switchman in a Number 5 Crossbar; the machine dropped *trouble tickets* — a physical, unignorable artifact of a should-not-happen, "nothing routine in this box." Our equivalent: a logging flag `system_alarm=True` (default False) that, besides the log line, **drops an unignorable artifact**. Flat-file at `~/.unseen_university/operations/system_alarms/<signature>.json` (NOT Postgres — a DB-down event must itself be alarmable). Deduped by **signature** (the subject): the 2nd/Nth of the same signature increments a count, never a new file. Each alarm carries a **caller breakdown** `{caller: count}` — the fix-list / punch-list — plus aggregate count and first/last_seen. On close it moves to `operations/system_alarms/archive/` (the "is this chronic?" history). **Self-clearing:** a caller quiet past a window drops off the breakdown; an emptied alarm ages out — the alarm *disappears when the problem is actually fixed*, which is also how you know it is. A new/reopened alarm fires the loudest channel (email+push) → the nudge pulls Akien to `uu alarms` (zero-inference view; `uu alarms --history` reads the archive). General, not inference-specific — every device gets unignorable failure surfacing. (Name: **system_alarm**, not "trouble ticket"/"ttix" — that was the metaphor.)

**2. Requests become tiers.** Callers pass specific model IDs (`anthropic/claude-haiku-4-5`, `llama3`, `gpt-4o-mini`) — fragile as models churn, and the source of the dead-fallthrough error. They should request a **tier** (capability), and the router (already built — `rules_engine.route()`) resolves it to the best available provider. When a specific model IS named: resolve to **best-available-match** within the tier (safety net, never fail closed), and — outside a sanctioned testing context — **drop a `specific-model:<model>` system_alarm** whose caller-breakdown is the authoritative punch-list of fragile call sites to fix. Testing (e.g. proving the proxy itself) is the legitimate exception, gated by an explicit request-scoped flag set only by self-test/pytest fixtures, never an inherited env var.

**The live bug (narrow, now proven):** device.py's unknown/explicit-model dispatch fallthrough hardcodes the dead `openrouter` source → error. Fix: route all paths by tier through the working rules engine; fall through on hard-error AND empty-response (worker/designer → google_free/gemini-2.5-flash returns EMPTY, proven this session, while ollama_cloud returns text); make liveness reflect dispatch-ability not just ping (local ollama reports available=True but its chat 404s).

**Reconciliation of D-inference (same day):** reading sources.py disproved that decision's premise (the provider/billing/router layer was already built 2026-06-12). So: CANCELLED T-inference-provider-billing-model, T-inference-policy-router, T-inference-request-priority-split (built); ABSORBED T-inference-route-fallthrough-on-hard-error → T-inference-resolve-requests-by-tier and T-intent-extractor-graceful-degradation → T-callers-request-tiers; RE-POINTED T-inference-liveness-and-loud-fail + T-inference-honest-degradation to consume the system_alarms primitive; KEPT T-inference-usage-cost-gate (low pri) + T-inference-strip-or-cost-rules.

**Proof factoring (advisor catch):** the "Igor round-trip returns text" proof does NOT live on the routing ticket (Igor's tier maps to the broken worker/designer path). T-inference-resolve-requests-by-tier proves a *tier-routed dispatch* (analyst→ollama_cloud) returns text — provable today. The end-to-end **Igor round-trip proof lands on T-callers-request-tiers** (after the routing + empty-fallthrough fixes ship).

**Alternatives rejected:** (a) Step-0 patch then redesign — no, the layer's already built; the real work is narrow + the alarms primitive. (b) keep silent-log failures — violates CP1 and is the disease itself. (c) per-caller alarm tickets — rejected for one-ticket-per-subject with caller-breakdown (the whole blast radius in one view). (d) delete-on-close — rejected for archive-on-close (recurrence is signal).

**Refs:** devices/inference/{sources.py,rules_engine.py,device.py,models_registry.py,self_test_sources.py}. Paths pre-approved by Akien (operations/system_alarms/ + archive/). Relates to [[feedback_check_new_paths_unseen_university]], [[project_mac_studio_inference_host]], feedback_inference_proxy_only. Cross-ref D-skills-two-products-2026-06-23 (`uu` dispatcher hosts `uu alarms`).

## Update 2026-06-23 — notify reshaped (email/push → visual surfacing)
The original T-system-alarms-notify (email+push via the loudest channel) is **cancelled**:
google_secretary (the only email path) doesn't work and was taken off the list (it kept
alarming Sonnet). The loud channel for a new/reopened alarm is now **visual surfacing**, split:
- **T-system-alarms-fatal** (DONE, proven @ 87de2157): `raise_alarm(fatal=True)` reports then
  raises `SystemAlarmFatal`; fatal=False stays fail-soft.
- **T-system-alarms-web-panel**: a conditional ALARMS PANEL (renders only when an alarm exists)
  on all web pages + indicators on the Akien/CC/Skeleton/Vetinari status panels + Control Station,
  reading the flat-file store directly (keeps the primitive's resilience). Needs a web-UI pass.
- **T-system-alarms-tmux-nag**: out-of-band tmux send-keys nag for workers running in tmux, one
  per new/reopened (not per increment).
Also: the loguru/stdlib-intercept ownership was moved Igor→base as a prerequisite cleanup
(T-loguru-ownership-to-base, proven @ 031295ea) — the alarm primitive logs through that base logger.
