# Device roster — who owns what (AUTHORITATIVE mirror)

Mirrored from instance-local builder memory (T-builder-memory-repo-residency). If a ticket
or doc contradicts this, the ticket is wrong — fix it at source. Consult this FIRST before
inferring a device's role from scattered tickets. Refreshed 2026-06-17; mirrored 2026-07-08.
produced_by: T-builder-memory-repo-residency

| Device | Role |
|---|---|
| Igor | Cognition + memory ONLY — reasoning, NE, dreaming, memory palace. Hosts no other device's maintenance work. LOWEST-priority agent; coding retired. Live instance: Igor-Wild1 (Wild0 archived). |
| Granny | Ticket dispatch / worker routing / dependency-gate orchestration (see rules/dispatch) |
| Nanny Ogg | Pure cron/scheduler — no inference |
| Scraps | Junk drawer of non-inference maintenance — log backfill, doc upkeep, validation. Capabilities incubate here, then calve into their own rackmounts. |
| Hubert | The Dev Lab + process owner — devlab/, tickets, decisions, constraint decoration. NOT "the Auditor" (auditing is a capability, not his identity). |
| Vetinari | Strategic/external-world governor (reserved) |
| Ponder | TBD |
| Librarian | MCP-facing memory/search fascia; curates + quality-logs recall |
| Evaluator | LLM judge-panel rack device (3-judge) — what T-judge-agent became |
| Critic | Lives in the WEB SERVER (Planner+Executor+Critic) — runtime critique surface; extend, don't rebuild |
| CC.0 / CC.1 | Claude Code: CC.0 discuss/orchestrate (long-lived), CC.1 the one standing builder |
| DickSimnel.0+ | Builder worker device (role=builder); DS.0 runs inference off-box on Hex |
| Aider.0 | Builder rack device (devices/aider/) — the proven near-term swarm builder |
| Vault | Credential home (devices/vault/): Fernet + Postgres, device-scoped, connect-time fetch. Secrets live HERE, not in shell files (bootstrap DB secret excepted). |
| Web Server | UI / fascia pages (feeds named per the Murderbot metaphor: shared → "Public") |
| Hex | Mac Studio M1 Max 32GB at 10.0.0.100 — local inference host (Ollama); the escalation box, not a device |

Class → instance addressing: `to=` is always an INSTANCE (DS.0, Igor-Wild1, GrannyWeatherwax);
class (granny/ds/igor/claude) is metadata — kind, role, memory-sharing model. Roles for
routing: apprentice < builder < creator < master (CC) < guru (Akien).

Why this note exists: roles drift into tickets wrong (a cancelled ticket once described
"Hubert (Auditor)" — doubly wrong), and a model with no authoritative roster infers identity
from that noise. This mirror is the prior that overrides it — and unlike the local memory,
a fresh builder on any box can read it.
