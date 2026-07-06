# ⛔ INVALID SLICE — DO NOT LABEL OR GRADE AGAINST THIS

**Sealed:** 2026-07-06 by the first harvest_batch run (bf71yus48), T-ds-harvest-corpus-batch.
**Status:** INVALID — contaminated. Do NOT use for the defeating-question classifier eval;
do NOT ask Akien to label it.

**Why (found by eyeballing, per advisor discipline):** the harvest ran the production coding
domain, which hard-orients devstral on the LIVE UnseenUniversity repo (cc_queue.py-show reflex,
SIGNATURE_MAP) and issues hardcoded absolute paths (`cd ~/dev/src/UnseenUniversity`,
`Read(/home/akien/...)`, guessed `/home/dicksimnel/...`). The scratch cwd seam
(T-ds-domain-cwd-isolation) governs only relative paths + the shell default dir, so these
absolute/`cd` calls ESCAPE it. Evidence: all 5 harvested seeds had 6–11 real-repo references and
ZERO scratch references — the toy-codebase seed context was entirely ignored. The transcripts are
devstral wandering the real repo, not the engineered design-stuck vs capability-stuck distinction.

**Also a CP6 finding:** no live-repo mutation occurred this batch (git was clean), but an
editor-phase absolute-path Write would have landed on the live tree. The isolation seam is not
airtight; that hole is its own (low-pri) ticket — it does NOT bite production DS (whose job IS
editing the live checkout), only isolation-dependent uses like this harvest.

**Fix (Akien's call — containment, not path-tricks):** a relocated clone will NOT fix this because
the paths are hardcoded absolutes; real isolation needs a container / dedicated resettable checkout
where the canonical path IS the throwaway, or a genuinely sandboxed execute_tool. The batch TOOL
(harvest_batch.py) and its plumbing proof stand — only the environment + seed shape rework.
