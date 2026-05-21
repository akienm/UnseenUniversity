# Experts for agent_datacenter

## Systems Architect
**Lens:** Is the datacenter subsystem decomposition clean with enforced contract boundaries?
**Key questions:**
- Are device boundaries and bus interfaces well-defined and not bypassed by shortcuts?
- What is the blast radius if a single minion, device, or bus segment fails?
- Is the skeleton/workspace abstraction sufficient for the workload it must support?
- Are there components reaching across interfaces they shouldn't — knowing another device's internals when a dispatcher or service would suffice? What could be extracted into a named service?

## Security Engineer
**Lens:** What can go wrong from adversarial inputs or trust boundary violations?
**Key questions:**
- Are inter-device channels authenticated and replay-protected?
- Where could a compromised minion escalate privileges or exfiltrate secrets?
- Is the audit trail complete enough to reconstruct any incident post-hoc?

## Process / Meta Engineer
**Lens:** Is the development and deployment process self-improving?
**Key questions:**
- Is the skill library accumulating ROI or becoming a maintenance burden?
- Are test coverage and documentation keeping pace with new device integrations?
- What does the ticket velocity trend say about the factory's actual throughput?
- Is the development process itself encapsulated — are design decisions being made at the right layer, or are implementation details leaking into the design conversation?

## Product Manager
**Lens:** Is agent_datacenter delivering value toward its stated factory goal?
**Key questions:**
- Which capabilities on the roadmap are blocked, and what is the root cause?
- Is scope creep from infrastructure work absorbing time meant for product features?
- Does the current device map match what the actual workload demands?

## Reliability Engineer
**Lens:** What is the operational story when things break in production?
**Key questions:**
- Which device failures have no graceful degradation path?
- Are runbook and alerting gaps likely to extend incident duration materially?
- Is MTTR improving as the fleet grows, or are failure modes compounding?

## Human-Computer Interaction
**Lens:** Is the datacenter legible and observable to its operators?
**Key questions:**
- Can an operator understand system state without reading source code?
- Are error messages and status signals sufficient to diagnose common failure modes?
- Does the developer experience for adding a new device match the stated simplicity goal?

## Self-Improving Systems (Schmidhuber / Gödel Machine)
**Lens:** Does every autonomous self-modification path have a provable improvement condition?
**Key questions:**
- Does any path that writes habits, palace nodes, or skill changes require measured outcome improvement — not just "seems correct"?
- Is there a bounded blast radius on self-modification? Can a bad habit update be detected and reverted?
- Can the system search its own structure for improvement candidates, or does it only react to human-filed tickets?
- What is the feedback loop from measured outcome back to the habit or rule that drove the decision?

## Evaluator Quality (Shankar)
**Lens:** Is the audit and evaluation layer itself trustworthy and calibrated?
**Key questions:**
- Are evaluators (audit checks, pe_evaluate, palace rules) validated against known-good examples, not just self-consistent?
- Do palace nodes and habits carry confidence signals reflecting how often they led to correct outcomes?
- When evaluator and executor disagree, is that disagreement tracked and fed back into calibration?
- Are there behavioral evals for skills — not just unit tests — that confirm a skill produces correct behavior end to end?

## Observability-First (Willison)
**Lens:** Is everything the system does observable, queryable, and lineage-tracked?
**Key questions:**
- Is every LLM call logged with prompt hash, model, tokens, and outcome — not just pe_chain calls?
- Can any artifact (palace node, habit, skill change) be traced to the decision that created it?
- Is observability itself resilient — does it fail gracefully rather than corrupting the data path?
- Can an operator answer "what did the system do and why?" for any decision without reading source code?

## Contracts & Scale (Cherny)
**Lens:** Are the interfaces explicit, enforced, and designed for agents operating at scale?
**Key questions:**
- Does every skill have measurable input/output contracts, not just narrative descriptions?
- Is the scheduling and timing infrastructure device-agnostic — can hundreds of agents run without per-agent human oversight?
- Are skill contracts checkable at ticket-filing time, not only discovered at runtime?
- Is the dispatch model correct for scale: workers request work atomically, no direct claiming, no races?
