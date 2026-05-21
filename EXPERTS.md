# Experts for agent_datacenter

## Systems Architect
**Lens:** Is the datacenter subsystem decomposition clean with enforced contract boundaries?
**Key questions:**
- Are device boundaries and bus interfaces well-defined and not bypassed by shortcuts?
- What is the blast radius if a single minion, device, or bus segment fails?
- Is the skeleton/workspace abstraction sufficient for the workload it must support?

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
