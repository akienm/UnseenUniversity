# Deterministic AI Development: Levers - A layer on top of skills.

*Akien Maciain, Test Automation Architect*

---

## Contents

- Contents
- Introduction
- Levers
- Workflow
- The Audit Pyramid
- Full Skills List (Alpha)
- Tracking
- For Additional Background

---

## Introduction

Models misbehave most when given the most latitude. In that regard, skills are wonderful tools. Skills are like checklists of things to do. They keep the model very focused on doing just what you want.

Skills by themselves do not complete the work though. Skills work best when part of an overall workflow that handles design and implementation.

My own experience is with building [akienm/TheIgors](https://github.com/akienm/TheIgors). An ongoing research project into using graph trees for reasoning. ~200K lines of code, which works, and has only ever been seen by Claude. I've reviewed maybe a dozen lines. But I designed it, not Claude.

While these and a lot more details are covered below… The stand out feature of my AI experience is LEVERS.

In aggregate, they allow me to treat Claude as a savant. Brilliant in some ways, but forgetful in others. And not fight that, but build around it -- a resilient structure that keeps its forgettings from becoming an issue. I still have to keep a lot in my head. But it takes care of the rest of the work.

---

## Levers

Levers are ideas, practices, tools, phrases or questions that provide outsized benefits. I am *constantly* looking for the levers in every part of my life.

I am very successful in test automation because early in my career, I found the levers that made it easy (change isolation via layers). I am always looking for the lever.

In AI, skills are one such lever. An important one. So is context size.

But there are so many more. These are most of my keystone patterns:

1. A notebook. Mine's a text file. There are log files that capture all the chats and so on. These are the points or ideas that stood out so I don't have to find them again.
  1. It's for writing if it's gonna be more than a sentence. Too many times I've accidently cleared Claude's terminal client on Linux. Better to compose it an editor and paste it in.
  1. It's also for blocks of stuff I might have to paste in more than once (our mission when he's asking for local GPU for instance).*
  1. Commands to paste
  1. Key moments in the project.
  1. It does not have to capture everything. There are other logs for that.
1. My own programming and architectural experience -- Can't be understated. Per the design of the experiment that TheIgors is, I don't read almost any of the code. But I do watch the kinds of bugs that come out of the system, and how Claude goes about fixing them. I will often halt him if he's going the wrong way, and tell him how I'd rather have it done. Keeping and holding that big picture. Specifically:
  1. Claude will tend to reuse out-of-date information
  1. Claude will tend not to centralize concerns to minimize maintenance
  1. Claude will tend to get lost in the weeds in some circumstances
  1. Claude won't ever remember that he fixed the same thing yesterday. Or last week.
1. Process from the start -- Us humans seem to have a love/hate relationship with process. But not Claude. As long as it's spelled out in the skill, he'll follow that to the letter. But this means: Everything gets designed, the designs audited, testing added (Test Driven Development - and no flaky tests allowed to remain unresolved), designs approved, ticketed, the tickets audited, sprinted, the plan audited, the code built, the result audited, the planned tests run, the result audited, the ticket closed.
1. Project state information -- Is all kept outside of the model's context. In our postgres database. With embeddings.
  1. Igor has semantic search internally. So Claude can do semantic searches inside of igor for every follow on piece of data after CLAUDE.md, for zero tokens. It contains:
    1. Project overview.
    1. Available tools and skills
    1. Tickets, epics, slates (echoed to github at day's end)
    1. Project documentation tree -- in the repo (architectural, .md files) and in the code (tops of files for architecture of that component, and inline for operational notes). Minimal token cost for Claude to get up to speed.
    1. Ticket state information is written into the titles: NEEDS DESIGN, DESIGNED, BLOCKED, READY, CLOSED, a few others. Because Claude (Sonnet) will keep picking up completed tickets at random times as if they still needed work.
    1. Decision, history, etc.
  1. State changes are saved immediately and durably. Ideas, designs, decisions, tickets, and the day's slate (sprint).
  1. The mode's context is disposable. Tho I will take related tickets in a row for discussion before clearing context.
1. Tooling and Automation Tools -- The things the LLM will use…
  1. Claude itself is tooling. To development processes.
  1. Audits -- Audits review the designs or code and force mistakes and ambiguity out of it. See The Audit Pyramid below. Gets it's own section.
  1. External Tools Internalized -- The toolset can include more than just Claude
    1. I can ask Claude to go to a web page and scrape something for me. It might install libraries and run a small script inline to go get the data. And the next time, it'd do that again. Claude is constantly building it's own tooling, And then trying to throw it away. And I'm constantly telling him to generalize it for reuse.
    1. Igor uses a layered browser automation library ( https://github.com/akienm/swadl ) to build durable automation that is easy to maintain. Zero tokens to run a path a second time except in result interpretation.
  1. Logging tree -- The base class for all objects in the system handles all logging and introspection. All objects contain all the logging calls. The logs are forensic. Each state change with systemic implications, and all major component boundary crossing is logged. Each monitoring point added as a result of a previous logging failure as well. The logs themselves are managed in a tree, so a timestamp from a failure in a chat log leads back to the correct point in the master log.
  1. MCP -- MCP is basically a mail service between models and similar components. More tooling. But your agent or whatever has to watch for those messages. By itself, MCP doesn't do anything. I have an MCP channel between Claude Code and Igor and it's hard to get Claude to use it! Which brings us to…
  1. Capabilities -- Software like Claude Code and Aider give lists of capabilities to the models they attach to. This then becomes the most certain way to have an LLM something exactly the way you want it and when you want it. Wrap it in 'capability'.
1. Powerful Questions -- This brings things into view that you just can't imagine before hand. For all of these, I lay out my own ideas first. Then I ask some or all of the questions below:
  1. What have I left out?
  1. What have I missed?
  1. What would be more efficient?
  1. What best practices or patterns could we use to good effect?
  1. What could we do better?
  1. How do other people do this?
1. Rob -- One of my first learnings was to ask Copilot to create 'Rob'. A developer who's so grateful they finally have a QA person on the team! And I 'rode on Rob's shoulder for a 'selection of activities such a developer would have on a day to day basis'.
1. About Claude
  1. Sessions -- Even though I work in long sessions, I am constantly saving state. So whenever the Claude window reaches a good stopping point, we have a generic compact and preserve that allows it to pick up where it left off: /compact preserve: Read today's slate: ~/.TheIgors/claudecode/YYYYMMDD.slate.txt. In-flight and Next: see slate.
  1. Claude tends to be 'impatient'. -- Make sure you point that impatience well. He'll tend to defer things or give you a fast way to solve it that's not a well thought out way in an architectural sense. I've over and over run into 'I thought we'd implemented that! No, I deferred it because xyz.'
  1. Focus -- Keep in mind that even large contexts aren't infinite. LLMs function well on summaries. Our documentation summarizes rules and gives pointers to more info if needed.
  1. /advisor -- This is a built in tool for Claude Code: It allows Sonnet to use Opus when it's uncertain. Makes the lower level much more powerful to be able to call on the higher one.
  1. /loop -- This is a built in tool for Claude Code: Allows looking in on something periodically
  1. /schedule -- This is a built in tool for Claude Code: Tell Claude to do a thing at a specific time
  1. /simplify -- This is a built in tool for Claude Code: Review the code and simplify it if possible. Built into our audits.
  1. --dangerously-skip-permissions -- This is a built in tool for Claude Code: Command line switch. Keep it from nagging you. In a project where I literally have not looked at the code, not being asked about it constantly is necessary.
  1. Instructions in chat ≠ Instructions in skills. The latter is a checklist, the former is more what ye' might call guidelines…
1. And of course, my own skills based workflow. Which has been built by asking lever questions like 'How are the best in the field doing this right now? What's been published on this? How could we do better?'
1. Hypothesis extraction and consequence checking -- Every design decision (/sorted) now formally extracts a testable hypothesis before tickets are filed: three questions must be answered (1) Which goal does this serve? (answer with G-xxx or "none, reason"), (2) What should be observably different after these tickets ship? (plain English, falsifiable), (3) How will we know? (metric, log line, behavior, or eval). This ties intent to a verifiable outcome. For M/L/XL decisions, a gated consequence-check ticket is auto-drafted — a structured prediction of potential unintended effects, what signals to watch for, and a gate condition (date or observable event). When the gate clears, the ticket surfaces as actionable. Consequence-checking becomes a tracked work item, not an informal afterthought.
1. Encapsulation as first-class design principle -- Workers request work via encapsulated black-box interfaces; subsystems do not reach across boundaries they shouldn't; callers don't know internals they don't need to. This appears as: (a) dispatch IS assignment (queue is black box, workers call "anything for me?" periodically), (b) audit-design Check 10 (encapsulation surface — detects subsystems knowing internals they shouldn't), (c) systems architect + process/meta engineer experts explicitly scan for coupling that could be a service. The pattern extends beyond code (the database proxy, inference proxy, MCP channels, ADC device model) to work management itself — Goal 3.7 (shared task service) generalizes this for multi-agent platforms.
1. Serious goal tracking -- Goals are formally tracked in the database via goal_adopt, goal_scan, and goal_close tools, with G-xxx identifiers assigned to each goal. Goals persist across sessions. Every /sorted links back to a G-xxx goal via testable hypothesis extraction (question 1), or explicitly documents "none, reason." This creates a traceable line from goal → hypothesis → decision → ticket → commit — the system can report what changed in service of which goal.
1. Self improvement -- Use the tool's strengths to improve the tool itself. The audit-of-audits (/audit-audits) examines results from all audits to find patterns in what's being caught, what's being missed, and where the process itself can sharpen. Skills are edited based on what Claude repeatedly gets wrong. The system is always improving its own operating procedures.
1. Design around the tool -- Claude has real strengths and real weaknesses. Design explicitly around both. Don't fight the weaknesses; wrap them. Claude will reuse stale information, lose the big picture, forget yesterday's fix, and defer architectural decisions when under pressure. Each of these has a countermeasure: decision tracking, palace memory, state written outside context, process encoded in skills, audits that catch drift. I'm constantly saying 'I know you don't remember this, but yesterday we decided...' And that's even with decision tracking and memory in place. Each bit of extra clarity costs tokens. The design question is always: what does it *really* need to know to do what's in front of it?
1. Temperature -- If the model is producing too many errors or getting too creative in the wrong direction, ask it to lower its temperature. Temperature is a model parameter between 0 and 1: higher values produce more creative and varied output, lower values produce more focused and deterministic output. When you need reliable execution of a known procedure, lower temperature helps. Most users never touch this, but it's a real lever.

- To be fair, we periodically sweep the skills and tighten around his forgetfulness better. May 3 '26 we added flags like CLOSED: to the beginnings of the ticket names once closed. He kept adding tickets back to the day's slate that were already closed. Now he sees it even if he only looks at the title. Yet another skills edit! Constantly optimizing.

---

## Workflow

My workflow mostly falls out like this:

/context-load -- the agent reads the project overview, the list of available tools, palace rules, and today's slate. The agent gets centered. About 2K tokens.

/design -- we work our way through any tickets that are blocked.

/note -- adds any random note that might be important later in the day's slate.

/ticket -- any issues that come up along the way so we address them later.

/sorted -- all issues under discussion are now resolved enough to go to sprint. Before filing tickets, /sorted extracts a testable hypothesis with three mandatory questions: (1) which goal (G-xxx or none, reason), (2) what observable difference, (3) how will we know. For M/L/XL decisions, it also auto-drafts a gated consequence-check ticket with predicted unintended effects and a gate condition. Then launches /audit-design to validate the design against 11 positive checks (goal frame, success observable, alternatives considered, constraints named, closing-pass done, no conflicts, palace-rules honored, scope decomposed, executor/tier named, encapsulation surface clear, no unbounded CC blackout path). Tickets anything we've been talking about that isn't ticketed, runs /audit-ticket on each draft, and gets it ready for sprinting.

/sprint / /sprint-batch -- sprint a ticket or sprint a large batch of tickets. Sprint-batch calls sprint over and over. Topo-sorts by dependencies before starting. Per-ticket: capability-check → verify in_progress status (dispatch IS assignment, no claim) → infrastructure brief → pull+work → cleanup → test → commit+push → close → /savestate. /sprint also calls /savestate on completion.

/audit-precode -- runs automatically between plan approval and the first edit in /sprint. Validates that named files exist, symbols exist, preferred-paths rules are satisfied, and the test plan is named. Haiku-speed. Escalates to Sonnet on high-inertia touches.

/fixit -- shorthand for /sorted + /sprint-batch in one go.

/day-close -- cleans up, runs /audit-day, closes out the day's slate.

---

## The Audit Pyramid

We have a family of scoped audits, each targeting a different failure class and running at the cheapest model that can reliably catch it.

**Pre-filing** (/audit-ticket, Haiku): runs on every ticket draft before it lands in the queue. Duplicate detection, already-done-in-code check, HIGH-inertia pre-approval gate, scope-creep split, build-tightness grade, design-rule checks (palace-loaded at filing time). See Tickets section above.

**Pre-code** (/audit-precode, Haiku → Sonnet): runs between plan approval and first edit. File/symbol existence, HIGH-inertia reaffirmation, preferred-paths compliance, test plan named, docstring plan, diff-size vs ticket-size estimate.

**Post-code** (/audit-smell, Sonnet): runs after code is written, before tests. Checks for premature abstractions, bespoke logic where a standard pattern exists, missing log calls, misleading names, over-complex conditionals, and test shape adequacy.

**Post-build debris** (/audit-debris, Haiku): cleanup pass after commit. Temp/artifact files, debug prints/breakpoints, log-size growth, test DB cleanup (live rows in test schemas), file placement, docstring rot on touched load-bearing files, subsystem index drift, commented-out code.

**Daily cross-session** (/audit-day, Sonnet): run by /day-close. Inherits all 18 day-close-audit static checks plus: fix-one-leave-many sweep (function signature changed in N callers but M others missed), watch-for notes from prior runs (hit/age/expire), subsystem index vs. reality, inertia tag drift, TWM coverage gaps, habit health. Auto-drafts a scan-for-rest ticket to /tmp/ when fix-one-leave-many is detected.

**Design-gate** (/audit-design, Opus): runs at the opening of a /sorted block. Reviews the design against 11 positive checks: positive-target goal, runtime-observable success, alternatives considered, constraints named, "what am I missing" closing pass done, no conflicts with recent decisions, palace-rules honored, scope decomposed into PRs, executor+inertia per piece, encapsulation surface clear (no subsystems reaching across boundaries they shouldn't), no unbounded CC blackout path (spawn/dispatch/escalate keywords require an explicit spend cap or block/hold routing — see `C-no-cc-auto-spawn`). Blocks /sorted Step 3 on AMEND until fixed.

**Expert panel** (/audit-expert, Opus): broadest-lens review. Each expert sees the whole codebase through their field's sharpest questions -- not "is this code clean?" but "is this system doing what this discipline demands?" Per expert: ≤5 severity-tagged observations, ≤2 watch-for notes (stored in palace with TTL ≤ 14 days), 0–1 candidate ticket drafts routed through /audit-ticket before filing.

| # | Expert | Broadest lens |
| --- | --- | --- |
| 1 | Cognitive Scientist | Is reasoning architecture consistent with human cognition models? |
| 2 | Systems Architect | Is subsystem decomposition clean? Coupling, cohesion, blast radius, encapsulation surface (subsystems reaching across interfaces they shouldn't?). |
| 3 | Safety Engineer | What are the failure modes? Runaway processes, unrecoverable states. |
| 4 | HCI Specialist | Is Igor legible to its users? Feedback quality, error clarity, trust signals. |
| 5 | Distributed Systems | Is the multi-instance design sound? Consistency, idempotency, clock drift. |
| 6 | ML Engineer | Is the learning architecture coherent? Feedback loops, distribution shift. |
| 7 | Process / Meta Engineer | Is the development process self-improving? Audit ROI, tech-debt rate, design decision encapsulation (are decisions being made at the right layer?). |
| 8 | Security Engineer | What can go wrong from adversarial inputs? Injection, secret exposure. |
| 9 | Reliability Engineer | What does the on-call story look like? MTTR, alerting gaps. |
| 10 | Data Engineer | Is the persistence layer sound? Schema drift, migration safety, lineage. |
| 11 | Product Manager | Is Igor making progress toward its stated goal? Velocity, blocker patterns. |

Cadence: weekly runs 3 random experts; monthly runs the full panel (with Ultraview on HIGH findings).

**Meta-audit** (/audit-audits, Sonnet/Opus): audits the audit pyramid itself -- watch-for TTL compliance, telemetry sampling uniformity, check confidence calibration, cadence adherence, findings-to-ticket conversion rate. Runs monthly or on demand.

All audit levels emit structured telemetry to the palace (unseenuniversity/audits/<level>/runs/<timestamp>). This creates a uniform time-series for trend analysis -- findings per week, checks fired vs. amended vs. discarded, watch-for hit rates.

---

## Full Skills List (Alpha)

/audit-audits -- meta-audit over all audit telemetry; checks cadence, TTL compliance, confidence calibration

/audit-day -- cross-day code health: inherits all day-close-audit checks + fix-one-leave-many sweep + watch-for management + telemetry

/audit-debris -- post-commit debris cleanup: temp files, debug artifacts, docstring rot, test DB cleanup, file placement

/audit-design -- design-gate review before /sorted: 11 positive checks (goal frame, success observable, alternatives, constraints, closing-pass, conflicts, palace-rules, scope decomposition, executor/tier, encapsulation surface, no CC blackout path)

/audit-expert -- 11-expert broadest-lens panel; weekly (3 random), monthly (full), on-demand by area

/audit-goal -- goal quality gate: 7 checks on any G-xxx goal before it's treated as authoritative; blocks vague or unmeasurable goals upstream of everything else; Opus

/audit-hypothesis -- hypothesis quality gate: 5 checks before /sorted files tickets; catches untestable claims, unobservable measurements, invalid goal links, contradictions with recently falsified hypotheses, missing time horizons; Opus

/audit-precode -- pre-edit plan validation: file/symbol existence, preferred-paths, HIGH-inertia reaffirmation, test plan named

/audit-smell -- post-code quality scan: premature abstractions, missing log calls, misleading names, bespoke vs. standard patterns

/audit-ticket -- filing-time ticket audit: duplicate, already-done, scope, HIGH-inertia gate, design-rules, build-tightness grade

/commit -- does the commit, pull (and merge), push

/context-load -- loads project overview, palace rules, today's slate, recent decisions, pending approvals, inbox

/day-close -- closes out the day: slate finalization, /audit-day, docs commit, GitHub Discussion, push

/day-close-audit -- static 18-step debris and hygiene check (tests, file placement, smells, registry, inertia, threads, logs, burn rate, schema, dead code, duplication, habit health, TWM coverage, dependencies, credentials, simplification, registered checks, wiring, capability-map drift)

/design -- design-mode session marker; writes DESIGN_START to slate, sets design_mode flag

/dream -- manually trigger Igor's dreaming pass via channel message; polls up to 30s for the dreaming summary response

/eval-run -- weekly capability snapshot: 5 behavioral questions about what Igor can actually do, independent of ticket velocity; feeds goal KR progress; run Fridays or standalone

/export-chat -- exports current Claude Code chat window to a dated markdown file (works around tmux scrollback limits)

/factory-create -- scaffold a factory spec from 6 questions (name, description, owner_id, members, eval rubric, daily budget) → produces config/factories/<name>.yaml; then calls python run validate and python run instantiate. A factory is one level above the agent scaffold: it declares a set of agents wired under a single orchestrator with a shared owner escalation chain.

/fixit -- shorthand for /sorted + /sprint-batch on the just-filed tickets in one go

/goal -- create, list, update, block, and retire G-xxx goals; the layer above decisions that anchors all design work to measurable outcomes

/note -- adds a random note to the day's slate

/outcome -- review a decision's hypothesis against observable evidence; records confirmed / falsified / needs-more-time; updates goal KR progress; closes the learning loop

/question -- parking lot for Q-xxx observations not yet ready to decide; survives compaction; questions can be promoted to hypotheses or decisions when ready

/readinbox -- reads Igor's inbox (messages from build processes, Claude, internal subsystems)

/savestate -- full session close: compose preserve string + inject compaction

/skills-sync -- sync skills between local (~/.claude/skills/) and the canonical repo; repo→local deploys managed skills, local→repo promotes a local-only skill into the canonical set

/sorted -- closes a design block → extracts hypothesis → batch tickets via /audit-ticket, writes decision to palace and log, appends to slate. Named /sorted rather than /decided because not every ticket actually requires formal design — but by the time you run this, whatever needed sorting has been sorted. The name also avoids models over-weighting the word "decided" and treating the command as more ceremonial than it is. (Underlying skill file is still named `decided`.)

/sprint -- per-ticket execution loop: capability check → audit-precode → infrastructure brief → pull+work → cleanup → test → commit+push → close → /savestate

/sprint-batch -- multi-ticket sprint: topo-sort by dependencies, shared setup once, per-ticket loop via /sprint-ticket, batch teardown with /autocompact

/sprint-loop -- autonomous queue drain with pre-scheduled wakeup; schedules ScheduleWakeup before each batch so compact mid-sprint can't lose the loop; terminates when queue is empty

/sprint-ticket -- single-ticket execution unit: capability check, claim, build, test, commit, close, savestate; called by /sprint and /sprint-batch. Step 5 now runs `repo_map.py` on affected files before the infrastructure brief — compact AST symbol map (classes, methods, top-level functions) for sprint orientation without reading full files.

/test-fix -- test/fix/test-again loop for failing suites

/ticket -- creates or updates a ticket; runs /audit-ticket on each draft before filing

/weekly-retro -- Friday hypothesis + goal review: confirmation rate, goal KR trends, priority changes for next week; called automatically by /day-close on Fridays or standalone

/workflow -- 30-second reference map of the full tracking and workflow system; run when you've been away, after compaction, or when you're not sure which skill to use next

---

## Tracking

We track goals, ideas, decisions, tickets, slates, occasionally epics, architecture, the code, and the memory palace. (Memory Palace is a memory tool that helps students study, and Claude/Igor to find things quickly and with fewest tokens.)

**Goal:** Goals are formally tracked in the database using goal_adopt, goal_scan, and goal_close tools, with G-xxx identifiers assigned to each goal. Goals persist across sessions. Every /sorted links back to a G-xxx goal via a testable hypothesis, or explicitly documents "none, reason." This creates a traceable line from goal → hypothesis → decision → ticket → commit — the system can report what changed in service of which goal.

**Ideas:** It's just a folder. One text file per idea. These are pasted in and discussed.

*Why:* Lets me brainstorm and chat with free AIs in web chats to sort details with no paid tokens. This isn't an implementation, it's an idea that will become a discussion.

**Decisions:** /design starts a design session.

This can be a pretty free form discussion. Decisions are where a point of design, a subsystem, or set of related tickets have their details worked out to a level of clarity and determinism that Haiku can reliably build it without fail.

*Why:* Because by doing the coding in Haiku (or Sonnet for medium-complexity work), I save tokens. By doing a detailed enough design, the smaller model can do it without needing the big brains of its big siblings.

One idea at a time can be pasted in, or they can be done in (usually) related groups.

Sometimes all we're deciding on is which open tickets to tackle next and in what order. This often looks like:

> "Of the open tickets, and using greedy ticket selection, which tickets remain open that are relevant to the goal and in what order should we tackle them?"

And then it's either I have questions or input, or /sorted.

The last questions I ask at the end of the design step are always at least:

- What am I missing?
- What could we do better?

*Why:* Because for all my experience, there's still plenty I don't know. These two questions have turned up all kinds of new things. They're amazing.

When it's done, /sorted.

Before filing tickets, /sorted extracts a testable hypothesis with three mandatory questions:
1. Which goal does this serve? (answer with G-xxx, or "none, reason")
2. What should be observably different after these tickets ship? (one falsifiable sentence)
3. How will we know? (metric, log line, behavior, or eval question)

This creates an entry in the decisions log and links the decision to a goal. Each decision also creates one or more tickets. As each ticket is created, /audit-ticket runs the filing-time audit:

- Is this a dupe with any other tickets?
- Is this already done in the code?
- Is this blocked by anything else that's pending?
- Is this well representative of the likely size of the work?
- Scope creep: should this be broken up?
- What is the passing condition?
- Which files will this touch?
- Any high-inertia files? (files whose changes might be high risk)
- Does the description match the title?
- What documentation will be updated?
- Do the design rules apply?
  - no-sqlite
  - oop-first
  - docs-in-code
  - no schema changes
  - all try/excepts will at minimum log the occurrence
  - names for variables and methods all describe what is being done
- What tests will we build/run to prove this works?
- Rollback plan for high-inertia file touches

Notes link the ticket back to its decision, and the decision is updated with its tickets.

**Slate:**

A slate is a day's work. Slates contain:

- In-flight -- what are we doing right now
- Planned -- what's still planned for this slate
- Ad hoc -- reactive additions
- Done today

When the day's slate is done, we run /day-close.

**Epics:** are just groups of related tickets. Like 'we're working on cognition today!'

**Architecture & Code:** Everything is documented at a high level in the project in MD files in the repo. The AI will read the architecture files to sort which files have to be modified. The actual documentation for each file will be IN THE FILE. At the top. So the AI can read it first. But all the key points like how things work -- that's in the project docs. Any ticket that updates the architecture has to update that tree of files.

*Why:* Saves tokens. Key points are read in root MD files, then the code files are read at the top, then the functional code itself. Minimizes the number of files that have to be looked at.

**Memory Palace:** A structured Postgres tree (unseenuniversity/rules/*, unseenuniversity/decisions/*, unseenuniversity/audits/*, unseenuniversity/infrastructure/*, etc.) that serves as the canonical index for conventions, rules, audit telemetry, and decision history. CLAUDE.md is a thin bootstrap shim; the palace is the source of truth.

*Why:* Palace nodes can be read individually (memory_get), searched (memory_search), or bulk-loaded by type. The model spends zero tokens re-deriving conventions it already decided.

**Preferred Paths:** A palace subtree (unseenuniversity/rules/preferred_paths/*) cataloging deprecated patterns alongside their preferred replacements -- e.g. raw psql calls vs. the MCP proxy, print() vs. the IgorBase logger, direct DB writes vs. cortex.store(). A scan tool watches 60 days of git history for regressions and surfaces candidates for review, never auto-filing.

**Levers Doc Sync Rule:** Whenever a skill file is edited or a new workflow capability is added, the levers doc must be updated to reflect the change before the sprint closes (sprint-ticket step 12.5). The levers doc is the single human-readable summary of how we work; it rots when sprints ship behavior changes without updating it. A forever audit check (levers-doc-skill-sync) fires LOW when skill files were modified in the last day without a corresponding levers doc update.

*Why:* Skills encode behavior; this doc encodes the design intent behind the behavior. When they diverge, new sessions have no way to understand why the system works the way it does.

---

## For Additional Background

- GitHub -- akienm/UnseenUniversity -- Generalized AI runtime substrate: PgBus (Postgres) message bus, device model, rack, installer
- GitHub -- akienm/TheIgors -- Igor: graph-matrix reasoning engine, persistent Postgres memory, habit scoring
