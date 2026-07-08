# Builder-memory triage — instance-local → repo residency

T-builder-memory-repo-residency (rebuildability-diff F2, CRITICAL). Every entry in
`~/.claude/projects/-home-akien-dev-src-UnseenUniversity/memory/` (146 files, read whole
2026-07-08) triaged: **(a)** already durable in the repo (decision/rule/architecture/code —
pointer given), **(b)** load-bearing process feedback → mirrored into a store artifact,
**(c)** personal/ephemeral/instance-mechanics → stays local. The local dir remains the
working cache; NOTHING was deleted there. produced_by: T-builder-memory-repo-residency

Artifacts created/extended by this triage:
- `rules/cc.0.dispatch.*` (NEW) — dispatch contract: handshake, roles, semaphores, concurrency, calm signals
- `rules/cc.0.autonomy.*` (NEW) — real gates vs hallucinated gates; walk-away model; working defaults
- `rules/cc.0.git-workflow.*` (NEW) — main-only, push-every-close + guard, ticket↔hash, test discipline
- `rules/cc.0.design-doctrine.*` (NEW) — cooperative/external-state/homogeneity/observability reflexes
- `rules/cc.0.safeguards.*` (EXTENDED) — HIGH-inertia gate conditional on Igor being LIVE
- `rules/cc.0.preferred_paths.*` (EXTENDED) — inference-proxy-only + skills-symlink entries
- `notes/builder-gotchas.20260708.md` (NEW) — proof_emitter mechanics, editable-finder, sweep scoping, envelope/body, session reliability
- `notes/device-roster.20260708.md` (NEW) — authoritative roster mirror + instance addressing

## Fresh-builder check — the 10 most load-bearing feedback memories, readable from repo alone

| # | Memory | Repo artifact |
|---|---|---|
| 1 | cc_concurrency_hard_limit | rules/cc.0.dispatch.* |
| 2 | granny_no_cc_spawn | rules/cc.0.dispatch.* |
| 3 | proof_emitter gotchas (+ cant-prove-refactors) | notes/builder-gotchas.20260708.md |
| 4 | git stash / checkout hazards | rules/cc.0.safeguards.* + notes/builder-gotchas.20260708.md |
| 5 | push_every_ticket (maxim + DISPLAY= guard) | rules/cc.0.git-workflow.* |
| 6 | ticket/decision envelope body.* trap | notes/builder-gotchas.20260708.md |
| 7 | stop_babysitting / intention_then_walk_away | rules/cc.0.autonomy.* |
| 8 | no_fresh_go / no_spurious_holds / no_unblocking_holds | rules/cc.0.autonomy.* |
| 9 | inference_proxy_only | rules/cc.0.preferred_paths.* (+ D-single-central-inference-proxy) |
| 10 | device roster (authoritative) | notes/device-roster.20260708.md |

## Triage table — feedback (79)

| Entry | Disp | Where / why |
|---|---|---|
| advisor_proactive | b | rules/autonomy (surface advisor at stall points) |
| ask_human_for_the_why | b | rules/autonomy + rules/design-doctrine |
| autocompact_dont_improvise | c | retired mechanism (native /compact); instance session mechanics |
| awaiting_validation_status | a | ticket_status.py + D-ticket-status-model (code is the record) |
| builder_never_sub_distributes | b | rules/dispatch |
| button_pattern | b | rules/design-doctrine |
| calm_signals | b | rules/dispatch |
| capture_first_fix_substrate | b | rules/autonomy (capture-first; skills-first stack priority) |
| cc_concurrency_hard_limit | b | rules/dispatch |
| cc_defaults_to_foreground_build | b | rules/autonomy (foreground = intent→tickets) |
| cc_owns_all_work | b | rules/dispatch |
| cc_plus_cookie | c | personal feedback-signal history |
| cc_plus_plus | c | personal shorthand (CC++ = well done) |
| check_new_paths_unseen_university | b | rules/autonomy (real gate #4) |
| clear_contextload_worsens_forgetfulness | c | instance session mechanics (tested 2026-06-26) |
| commit_to_main_directly | b | rules/git-workflow |
| complexity_limit_confabulation | b | notes/builder-gotchas (session reliability) |
| consequence_checking_gap | a | consequence-ticket machinery live (T-consequence-* + D-consequence-tickets-and-actionable-view) |
| cooperative_not_hierarchical | b | rules/design-doctrine |
| craftsmanship | b | rules/design-doctrine |
| default_draft_ticket_then_review | b | rules/autonomy |
| delegate_mechanical_sweeps | b | rules/autonomy |
| delete_after_successful_merge | b | rules/design-doctrine (migrations are two halves) |
| design_for_cc_mental_model | b | rules/design-doctrine |
| escalation_is_spec_quality_data | a | architecture/feedback-edges contract + D-feedback-edge-every-emission (dispatch-to-producer, under-specified-vs-novel) |
| exact_id_preflight_before_destructive | a | rules/safeguards working_safeguards (landed with T-rules-store-materialize) |
| export_not_copy | b | rules/design-doctrine |
| external_state_principle | b | rules/design-doctrine |
| flush_state_with_margin | b | notes/builder-gotchas (session reliability) |
| fresh_start_daily_compaction_tripwire | c | instance session mechanics |
| git_checkout_clobbers_uncommitted | b | rules/safeguards (law) + notes/builder-gotchas + rules/git-workflow (post-commit re-run) |
| git_stash_hidden_divergent_state | b | rules/safeguards (law) + notes/builder-gotchas |
| granny_no_cc_spawn | b | rules/dispatch |
| granny_restart | b | rules/dispatch (one line; launcher is repo code) |
| granny_shim_startup | b | rules/dispatch |
| ground_loop_is_passive_shim_owns_startup | b | rules/design-doctrine (+ architecture/ground_loop) |
| guru_loop_design | c | STALE — superseded by cooperative-not-hierarchical; kept local as history, do not apply |
| hold_ticket_drift | c | queue-hygiene observation, Akien-specific |
| homogeneity_over_special_case | b | rules/design-doctrine |
| igor_shutdown | c | ops detail (tmux graceful stop); Igor down |
| igor_tickets | c | Igor coding retired — validation rule moot; kept local as history |
| igor_uses_not_contains | b | rules/design-doctrine |
| inference_proxy_only | b | rules/preferred_paths entry (+ D-single-central-inference-proxy) |
| instrument_dont_probe | b | rules/design-doctrine (observability cluster) |
| intention_then_walk_away | b | rules/autonomy |
| mcp_restart_gated_on_readiness | c | one-time migration state, likely elapsed |
| monthly_opus_design_cadence | c | Akien's personal ritual cadence |
| native_compact_no_autocompact | c | instance session mechanics |
| no_fresh_go_for_live_runs | b | rules/autonomy |
| no_high_inertia_when_igor_down | b | rules/safeguards EXTENDED (conditionality clause) |
| no_holds_without_approval | b | rules/autonomy |
| no_igor_tickets | b | rules/dispatch (role=master, never worker=igor) |
| no_recommendations_in_narrative | b | rules/autonomy |
| no_scope_shrinking_do_it_right | b | rules/autonomy |
| no_skills_sync_symlink | b | rules/preferred_paths entry + rules/design-doctrine |
| no_spurious_holds | b | rules/autonomy |
| no_unblocking_holds | b | rules/autonomy |
| observability_value | b | rules/design-doctrine |
| open_questions_workflow | b | notes/builder-gotchas (Q1:/A1: convention; status per D-ticket-status-model) |
| orchestration_insight | b | rules/design-doctrine (shape-first) |
| overnight_minions | c | provisional + model landscape superseded (aider/Hex era) |
| overnight_tests | b | rules/git-workflow (test discipline) |
| priority_halt_usage | b | rules/dispatch (HALT path unused) |
| push_every_ticket | b | rules/git-workflow (maxim + full guard) |
| question_skill_is_cognition_questions | c | skill semantics; the /question SKILL.md description is the fix target (skill text is repo) |
| rewind_more_disruptive_than_compact | c | provisional instance observation |
| rewind_reorientation | c | instance mechanics; /recover skill in repo covers it |
| second_cc_builder_gated | b | rules/dispatch (CC.n gate + one-agent-at-a-time) |
| skills_in_repo | a | repo skills/ IS the state; symlink rule in preferred_paths |
| slash_sorted | a | rules/preferred_paths (/decided → /sorted) |
| sonnet_1m_autocompact | c | stale (autocompact retired) |
| sorted_not_decided | a | duplicate of slash_sorted |
| stop_babysitting_keep_moving | b | rules/autonomy |
| theigors_archive_location | a | consolidation complete (project_runtime_path_cleanup_state; ~/TheIgorsProject archive) |
| ticket_preexisting_failures | b | rules/git-workflow |
| ticket_status | b | notes/builder-gotchas (designed-in-conversation → sprint) |
| verify_divergence_before_collapsing | b | rules/design-doctrine |
| wait_for_the_better_lever | b | rules/design-doctrine |
| web_ui_igor_path | c | stale (IMAP-era correction); web polling facts live in code |

## Triage table — project (53)

| Entry | Disp | Where / why |
|---|---|---|
| adc_queue_device | c | STALE (describes retired spawn-based dispatch; contradicted by rules/dispatch) — kept local as history |
| aider_builder_viable | a | devices/aider/ code + D-fable-builder-empirical-program + aider notes in store |
| akien_stlouis_trip | c | elapsed personal calendar (2026-05) |
| arch_separation | a | CLAUDE.md (UU portable, no TheIgors imports) + architecture/ |
| automated_test_factory | a | horizon vision; tickets exist (T-*-agent stubs) |
| big_ticket_approach | b | notes/builder-gotchas adjacent — pattern named in rules/autonomy (delegation); primary record is Akien's pattern, local copy stays |
| build_packet_pre_inference_compiler | a | devlab/claudecode/build_packet.py shipped + proof 4e7308cd |
| class_vs_instance_addressee | b | rules/dispatch + notes/device-roster |
| compiled_inference_vision | a | architecture/compiled-inference-thesis + I-compiled-inference-residue intention |
| cortical_columns_thesis | a | architecture/compiled-inference-thesis + architecture/text-cortex-and-emotion-modulation |
| critic_already_in_web_server | b | notes/device-roster (Critic row) |
| definition_of_done_tool_disappears | a | D-per-project-split-and-contracts (FTP tests seed) + intentions |
| delta_uu_architecture | c | Akien's day-job context (informs, not mirrored — his call recorded in the memory itself) |
| device_roster | b | notes/device-roster.20260708.md |
| dispatch_model | b | rules/dispatch |
| ds0_leveling_north_star | a | D-ds-builder-leveling + intentions |
| ds_cost_constraint | a | D-dsimnel-cc-parity + D-or-tiered-cascade + inference registry code |
| ds_second_pass_aider_cline | a | tickets T-ds-second-pass-aider-cline-strengths + T-domain-model-temps + store note |
| editor_half_viable_bottleneck_upstream | a | store notes (fable-consult) + closed tickets; harness fixes landed |
| fable_extraction_before_enterprise | a | D-fable-window-altitude-agenda + executed window |
| factory_vision | a | intentions + decisions (factory-of-factories recorded) |
| filesystem_memory_store | a | devlab/runtime/memory/SPEC.md + memory_emit.py + rules/memory |
| goals_being_retired | a | D-intention-based-development + goal-purge tickets |
| goals_to_intentions | a | same lineage + two-clocks in day-close skill |
| granny_pull_dispatch_and_alarm_model | b | rules/dispatch (pull model, shim launches, alarm singleton noted in sources) |
| graph_trees_self_aware_telos | a | intentions store (the telos is recorded); architecture notes |
| ground_loop_web_server | a | architecture/ground_loop intention-point (authoritative) |
| hex_ollama_model_slate | a | inference registry code + D-inference-cost-optimizing-router |
| igor_graph_tree_reasoner_instance | a | architecture notes + intentions (reasoner-class interface) |
| igor_reset_cleanup_before_codex_tickets | c | sequencing state (hold Codex tickets until cleanup done) — Akien-held gate, local |
| igor_wild0_archived_wild1_live | b | notes/device-roster (live instance named) |
| inference_cost_optimizing_router | a | D-inference-cost-optimizing-router-2026-06-30 + shipped increments |
| inference_tier_not_model_contract | a | decision + Proxy code (tier contract enforced in device.py) |
| intention_compiler_reflexive | a | D-architecture-as-code-cognition-pipeline + intentions |
| intentions_as_living_entities | a | intentions/ category live + placement convention recorded there |
| mac_studio_inference_host | a | Hex live + registry; operational facts in store updates |
| murderbot_feed_metaphor | a | D-murderbot-feed-metaphor-2026-06-03 |
| ollama_cloud_goal | a | decisions (dsimnel-cc-parity; free-lineup) |
| one_lineage_consolidate_to_uu | a | consolidation executed (237 decisions + 99 slates absorbed; runtime_path_cleanup) |
| only_claude_edits_repo | b | rules/git-workflow |
| origin_story | c | project history (Akien's December-2025 origin) — narrative, local |
| pipeline_progression | a | D-proof-on-close lineage (batches→pipeline gates) |
| pipeline_trajectory_gate_removal | a | CLAUDE.md proof-on-close why + rules/budget (gate-removal staircase) |
| rewind_workflow_primitive | c | instance workflow mechanics (D-rewind-as-workflow-primitive exists; provisional pushback noted) |
| role_hierarchy | b | rules/dispatch |
| runtime_path_cleanup_state | a | executed; grep-gate test pins it (tests/contract/test_no_retired_runtime_paths.py) |
| self_improving_goal | a | I-self-improving-process intention (canonical) |
| symlink_debt | c | stale (TheIgors symlinks gone with consolidation) |
| theory_of_operation | b | rules/design-doctrine (every point carries its why) |
| ticket_status_model | a | D-ticket-status-model + unseen_university/ticket_status.py |
| watch_problems_auto_deposit_debt | c | igor-internal debt note; Igor deferred (igor-is-last) |
| worker_types | b | rules/dispatch + notes/device-roster |
| workspace | c | STALE (agent_datacenter-era layout; superseded by UU consolidation) |

## Triage table — reference (13) + incident (1)

| Entry | Disp | Where / why |
|---|---|---|
| cc_queue_list_hides_tickets | b | notes/builder-gotchas (+ T-cc-queue-list-hides-tickets) |
| cc_rewind_checkpoint | c | instance session mechanics (rewind menu driving) |
| compose_credentials_at_connect_time | b | rules/design-doctrine (credentials clause) |
| copilot_prebuild_maps_to_build_packet | a | dedup pointer; build_packet.py shipped — grep before re-filing |
| credentials | c | SUPERSEDED by vault (memory says so itself); kept local as history |
| editable_finder_beats_pythonpath | b | notes/builder-gotchas |
| proof_emitter_cant_prove_refactors | b | notes/builder-gotchas |
| proof_emitter_gotchas | b | notes/builder-gotchas |
| sweep_guard_extensionless_scripts | b | notes/builder-gotchas |
| symbols_table_priority | a | T-symbols-table-multilang shipped; priority signal noted in decisions |
| ticket_decision_envelope_body | b | notes/builder-gotchas |
| ticket_location | a | CLAUDE.md structural rules (one home) |
| vault_is_the_credential_home | b | notes/device-roster (Vault row) + devices/vault/ code |
| incident_superclaude_401 | c | incident record; fix shipped in bin/superclaude (env -u guard) |

## Counts

146 entries: 72 (b) mirrored, 44 (a) already durable, 30 (c) stay local.
Follow-on design question (out of scope per ticket): automation of future local→repo sync.
Noted here per the ticket's close requirement; no automation built.
