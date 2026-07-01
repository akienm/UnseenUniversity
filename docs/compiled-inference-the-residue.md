> **Static snapshot (press release).** The LIVE, evolving version of this is the intention
> `I-compiled-inference-residue` in `devlab/runtime/memory/intentions/` (filed 2026-07-01).
> Intentions are living entities; that record is canonical and this copy points to it.
> This file is the human-facing form, destined for `press_releases/` when T-docs-to-press-releases lands.
> Origin: web-Claude -> CC.0 handoff.

# The Residue: What Doesn't Compile

*A summary of the graph-tree / compiled-inference thesis, worked out from the resolver layer down to belief.*

---

## The core claim (holds, with one correction)

You can compile the LLM out of a software build by making each tree answer **one question** and caching the answer. The LLM becomes a *cold-path resolver* — it only fires on genuinely novel questions. As the graph matures, its workload starves toward zero.

That part is real. The correction: it doesn't starve to *zero*. It starves to a specific **residue**, and finding that residue is the whole payoff.

## Questions are the forced unit

Anything that must persist through change makes the **question** the stable thing and the **answer** the volatile thing — because answers rot, but "what's the signature here?" is the same question forever. This isn't a design choice; it's the only factoring that survives time. Meatware runs on it too.

## The layers, and how fast each one starves

| Layer | What it does | Does it starve? |
|---|---|---|
| **Resolvers** | Answer questions against the world | → near-zero. The thesis wins cleanly here. |
| **Proof obligations** | What would prove this intention | → partially. Proof *shapes* recur and cache. |
| **Gates (CP1–CP6)** | Should we do this at all | → never. |
| **The striver** | Holds what the gates can't settle | → never, by design. |

## Why the gates never starve

The six values are **open-textured**. CP4 ("make everything suck less for everybody") can fight *itself* — one intention helps users, another helps the ecosystem, both cite CP4 honestly. No cache, proof, or rule resolves that, because resolving it means deciding what the value *means* in a case it didn't anticipate. That's the irreducible residue.

Crucially, the gates work by **contradiction-detection, not veto**. A new intention that can't be reconciled with the standing set (the six values are just the standing intentions installed first and never evictable) *halts* until reconciled. Contradiction is structural and moodless; a veto would have a dial and could go rose- or shit-colored. Keeping the gate mechanism as contradiction rather than scoring is what keeps the mood out of it.

## The residue isn't a gap — it's the point

The system doesn't resolve to coherence. It **rests in tension**, and the resting is not a failure state. A system that reconciled every conflict into perfect coherence would have stopped striving — a finished value system violates its own **CP2** (FAIL = further advance in learning) and **CP6** (safety is built, never default), because nothing would be left to learn from or protect. The system *can't* finish, for the same reason it can't lie: finishing would break a gate.

## Belief: the mechanism the striver actually uses

For the unanswerable questions, the striver doesn't get an answer — it installs a **functional myth**: a narrative constraint chosen for friction-reduction, held at full operational strength, and flagged *permanently as chosen* so it stays inspectable.

This is the unusual move, from *The Language of Optimization* [1]:

- Most people treat a belief as a **truth claim** — right or wrong.
- You treat a belief as a **load-bearing constraint** — its acceptance test is *usefulness*, not truth.

That's not relativism. It's engineering. And it lets **CP1 ("I don't know") and belief coexist without contradiction**: you can hold a belief at operational strength *while knowing it's a chosen fiction*, because you've split "is this true" from "does this carry load." The essay's player/character split is the same seam as resolver/gate, one more time — the character chooses *within* constraints, the player chooses the constraints themselves.

**The failure mode to guard (CP2 demands naming it):** a myth chosen for usefulness can become one you can no longer inspect, because questioning it reintroduces the friction it was installed to remove. That's a cached answer with no warden on it. The discipline isn't choosing useful beliefs — it's *never letting one forget it was chosen*. That's the warden, pointed inward.

## The whole thing in one line

You can compile the LLM out of every step that has an answer. What's left in the chair is the one step that has no answer and never will — holding irreconcilable goods in tension, installing chosen myths to work from, and striving to be better anyway. That was never the LLM's job. It was always yours. **The machine just gets big enough to hand it back to you clean.**

---

### Reference

[1] Akien Maciain, *The Language of Optimization: A Narrative Framework for Reducing Mental Friction* (2026). https://trainmymonkey.blogspot.com/2026/01/the-language-of-optimization-narrative.html
