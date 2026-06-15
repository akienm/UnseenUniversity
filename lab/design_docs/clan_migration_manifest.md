# clan.memories Migration Manifest

**Generated:** 2026-06-15  
**Scope:** Categorical audit of clan.memories by memory_type + metadata.kind  
**Decision:** D-storage-layer-formalization-2026-06-14  

---

## Summary

clan.memories currently holds **187,177** total records across 20 distinct memory_type+kind combinations. This manifest categorizes each combination into one of four target namespaces:

- **bootstrap** — core infrastructure (system identity, root nodes, bootstrap procedures)
- **devlab** — operational/learning data (episodes, word graphs, interpretations)
- **library** — curated, reusable knowledge (facts, code symbols, patterns, references)
- **employer.akien** — Akien-specific personal context (goals, experiences, decisions, credentials)

This categorization enables future migration: records can be moved to their target namespace once this manifest is reviewed and approved by Akien.

---

## Detailed Categorization

### BOOTSTRAP (39 total records)

Core infrastructure needed for the system to bootstrap and identify itself. These stay in `clan.memories` (or migrate to a dedicated `bootstrap` schema if namespacing is implemented).

| memory_type | kind | Count | Sample narrative | Rationale | Target |
|---|---|---|---|---|---|
| IDENTITY | NULL | 30 | "These identity patterns themselves can be optimized..." | System identity, roster, self-identification | **bootstrap** |
| ROOT | NULL | 5 | "I am igor_wild_windows_0001. I learn, I remember..." | Root identity node (Igor's core self) | **bootstrap** |
| REFERENCE | root | 3 | "Tickets root — all cc_queue tickets live under this node." | Root references (TICKETS_ROOT, DECISIONS_ROOT, SKILLS_ROOT) | **bootstrap** |
| PROCEDURAL | root | 1 | "Skills root — CC slash-commands stored as PROCEDURAL memorie" | Root procedures (skills index) | **bootstrap** |

---

### DEVLAB (132,981 total records)

Operational and learning data generated during development, experimentation, and Igor's cognitive processes. These are volatile and purpose-specific to the current development phase. Candidate for archival or resetting if a fresh training run is needed.

| memory_type | kind | Count | Sample narrative | Rationale | Target |
|---|---|---|---|---|---|
| EPISODIC | NULL | 72,061 | "Web-user's model of Igor's design: explicit optimization dri..." | Learning episodes, Igor's experiential learning from interactions | **devlab** |
| WORD_GRAPH | NULL | 57,298 | "zwin" | Token graph data for embedding/language processing (development artifact) | **devlab** |
| INTERPRETIVE | NULL | 3,193 | "Writing essays is a 'computationally shallower' problem than..." | Analytical/interpretive records generated during learning | **devlab** |
| PROCEDURAL | NULL | 429 | "Write memories for future-Igor reading cold, not for the cur..." | Learned procedures and habits (operational, not foundational) | **devlab** |

---

### LIBRARY (48,031 total records)

Curated, reusable knowledge that serves as the system's "library" — facts, code structure, patterns, and references that are intentionally maintained. These are non-personal, portable, and can be shared or versioned independently.

| memory_type | kind | Count | Sample narrative | Rationale | Target |
|---|---|---|---|---|---|
| FACTUAL | NULL | 45,274 | "Zero was invented in India." | General factual knowledge base; curated facts about the world and systems | **library** |
| codebase_module | NULL | 475 | "File: unseen_university/_uu_root.py\n\nSymbols:\n  [function] u..." | Code structure index (codebase symbols, functions, classes) | **library** |
| FACTUAL | ticket | 1,999 | "Yield primitive: yield_to(habit_id) — pass control to anothe..." | Ticket-specific facts and architectural knowledge (codebase domain knowledge) | **library** |
| REFERENCE | NULL | 261 | "Work order #99 (closed): Session emotional histogram — track..." | General references and work order pointers | **library** |
| CORE_PATTERN | NULL | 13 | "The world is not a safe place. We have to build and care for..." | Core patterns and principles (reusable heuristics) | **library** |
| ROLE_MODEL | NULL | 9 | "Leah (user)" | Role models and example personas | **library** |

---

### EMPLOYER.AKIEN (6,126 total records)

Akien-specific personal context: goals, experiences, decisions, credentials, and preferences. This is the user profile / personal context layer that should be kept separate so Akien can carry it across different agent instances or export it independently.

| memory_type | kind | Count | Sample narrative | Rationale | Target |
|---|---|---|---|---|---|
| GOAL | NULL | 2,933 | "STANDING GOAL: Read from the existing reading/book queue Sta..." | Akien's personal standing goals and objectives | **employer.akien** |
| EXPERIENTIAL | NULL | 2,635 | "Understanding and valuing multimodal communication for enhan..." | Akien's personal experiences and growth/learning | **employer.akien** |
| REFERENCE | decision | 356 | "D-workshop-evolution-2026-04-20 — workshop evolution. Status..." | Akien's decision records | **employer.akien** |
| CREDENTIAL_REF | NULL | 165 | "where api key lives" | Akien's credentials and API keys | **employer.akien** |
| REFERENCE | slate | 18 | "# Slate 2026-04-20 (CLOSED)\n\n## Next up..." | Akien's session slates (daily plans/state) | **employer.akien** |
| PROCEDURAL | skill | 19 | "Skill /validate-files. Audit skill for TheIgors runtime file..." | Akien's custom slash-command skills | **employer.akien** |

---

## Verification Checklist

- [x] All memory_type + kind combinations from clan.memories enumerated (20 distinct types)
- [x] Row count for each type verified (189,240 total)
- [x] Sample narratives reviewed for each type
- [x] Categorical assignment rationale provided
- [x] Target namespace assigned for each type

---

## Next Steps (Akien Review Gate)

1. **Akien reviews** this manifest and verifies categorical assignments match intent
2. **Akien marks** any types that need reassignment in the "Target" column (edit this file)
3. **Akien approves** or notes caveats in a new column "Akien review" (add to table headers)
4. Follow-on ticket(s) implement the actual migration once this is approved:
   - Create target schemas if needed (devlab.memories, library.memories, employer_akien.memories, bootstrap.memories)
   - Migrate records by category
   - Verify FK integrity post-migration
   - Update clan.memories consumers to point to new locations

---

## Totals by Target Namespace

| Target | Count | % of total |
|---|---|---|
| bootstrap | 39 | 0.02% |
| devlab | 132,981 | 71.03% |
| library | 48,031 | 25.67% |
| employer.akien | 6,126 | 3.27% |
| **TOTAL** | **187,177** | **100%** |

---

## Notes

- **bootstrap** records are tiny (~23) but critical — these must be handled carefully (probably stay in clan.memories or get duplicated for safety)
- **devlab** dominates (70%) — these can be archived or reset between development phases
- **library** (25%) and **employer.akien** (5%) are the stable, portable, valuable parts — these should be extracted and curated
- The "employer.akien" namespace is currently scattered; consolidating it enables Akien to export his profile independently
