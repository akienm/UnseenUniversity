# Compiled Inference: The Real Cost of AI Development

*By Akien Maciain*

---

## The Creation/Ownership Gap

I've been building a reasoning graph tree inference engine. Because that's how humans work. We compile reasoning into habits. Habits are code, running in our heads.

Remember the first two minutes of driving? Too many things to pay attention to at once. But now it's easy. Driving has been compiled into a large network of habits. Code is just a set of steps with the ambiguity removed — or as much of it as we can remove and still have it mean something. The compiler jumps the gap through hard-coded rules. Which are themselves compiled reasoning.

AI companies have been raising rates. It's a hot topic right now, and for good reason — organizations are discovering that their AI spend is growing faster than their results are. The reason is almost always the same: they're paying inference prices for things that don't need inference.

Here's the gap nobody talks about when a manager says "I built an app in a day with AI":

**Creation cost:** one day. Genuinely impressive.

**Ownership cost:** every bug found in production, every flaky test, every "why is this broken now," every AI call that reinvents a wheel it invented last week, every inference dollar spent on something that could have been a script. Recurring. Compounding. Growing.

AI collapsed the cost of creation. It did not collapse the cost of ownership. Those are different problems, and only one of them shows up on the first invoice.

---

## Compiled Inference

If you do the same kind of work over and over — designing fixes for tickets, say — you could ask the AI in plain English each time. It parses your request, reasons about what to do, decides how to do it, runs the command, interprets the results, prepares a report. Flexible, capable, expensive:

**Freeform: ~1000 tokens**

Now if you do that often enough, you build a skill. A skill is the same reasoning written down as specific instructions. The AI no longer has to figure out the approach — it follows the checklist. Less reasoning, less variance, fewer tokens:

**Skill: ~300 tokens**

Take that skill and convert the deterministic parts to a script. All the AI has to do is call it and evaluate the result:

**Script: ~100 tokens**

Each layer has less ambiguity than the one above. For every step we can move outside high-level inference — to a skill, to a script, to a framework primitive — the AI becomes faster, cheaper, and more predictable.

This is not a new idea. It's what software has always been. Hardware is compiled reasoning. Books are compiled reasoning. Cars are compiled reasoning. The organizational habit of writing a runbook, a checklist, a standard operating procedure — all of it is the same pattern: take reasoning that works, remove the parts that don't need to be re-derived every time, and encode what's left.

The trick with AI is building structures that isolate change so the AI can *use* compiled reasoning rather than its own. Give it a constrained space — known nouns, known verbs, known extension points — and it becomes dramatically more reliable. Give it a blank canvas and it invents everything from scratch, every time, at full inference cost.

The quality engineering insight here is that this is exactly what layered test architecture has always done. Change isolation via layers is compiled reasoning for test automation. Page objects, flow objects, abstracted selectors — each layer removes a class of decisions from the implementation level. AI fits into that structure the same way a junior engineer does: it fills in the implementation within the constraints the architecture provides.

---

## Feedback Loops: How the System Improves Itself Over Time

The compiled inference ladder — freeform to skill to script — does not climb itself.

What makes it self-improving is a feedback loop with teeth. Not a retrospective. Not a post-mortem. A structured cycle that runs continuously:

1. **Form a hypothesis** — "If we encode this pattern as a skill, the error rate on this class of ticket will drop."
2. **Ship the change**
3. **Measure the outcome** — did the error rate drop? Did token cost fall? Did something unexpected break?
4. **Record the result** — confirmed, falsified, or needs more time
5. **Refine and repeat**

Each pass through the loop either compiles more inference (if the hypothesis held) or surfaces a constraint that needs to be understood before it can be compiled (if it didn't). Either way, the system knows more than it did last week.

The important thing is that the loop is explicit and tracked. Informal improvement — "we learned some things this quarter" — does not compound. Structured improvement with a hypothesis record does. You can see what you tried, what worked, what didn't, and where the remaining variance is coming from.

In my own work, every design decision links to a testable hypothesis before tickets are filed: which goal does this serve, what should be observably different when it ships, and how will we know. At the end of the sprint, the hypothesis is reviewed against evidence. The loop closes. The system's operating procedures are updated. The next week starts cheaper than the last.

This is the answer to rising AI costs. Not switching providers. Not prompt optimization. Building a system that progressively replaces inference with compiled reasoning — and measuring whether it's working.

---

## Tooling and Dos and Don'ts

### Build the tooling, then constrain the AI to use it

Claude Code itself is tooling. So are skills and scripts, and anything else built into the development environment. The last two days of my work have been spent refactoring skills to externalize as much as possible to scripts — because scripts are cheaper, more deterministic, and faster to run.

Concrete example: I can tell Claude to open a specific Chrome profile that's already logged into Gmail and return the first message (~200 words). That costs around 2,000 tokens. If I have a Python script using a test automation framework that reads the message and returns JSON, it costs 270 tokens. Same result, one-seventh the cost.

But that's just the token math. The more important point is reliability. A script does exactly the same thing every time. An AI reasoning from scratch introduces variance at every step.

We use Haiku for the implementation work, Sonnet for mid-complexity tasks, and Opus for auditing designs and reviewing the results of all the audits. Right tool for the right layer.

### Getting the AI to use the tooling

This is the other half of the problem. You can build all the tools in the world and the AI will still default to whatever it has the most training on.

My project has MCP access to a Postgres database. Igor (my reasoning engine) uses it correctly. Claude defaults to Bash. Why? Because if there's the slightest friction — the tool isn't top of mind, the context shifted, there's a small error — it falls back to the pattern with millions of training examples behind it.

Telling it what you want is only half the battle. The rest is telling it what *not* to do, and putting that instruction where it will be read. Build the skill, then put "use /myskill instead of bash for this class of operation" at the top of CLAUDE.md. The constraint has to be as prominent as the capability.

This is a general principle: AI defaults to the path of least resistance in its training data. Your job as the architect of the system is to make the right path the path of least resistance. That means building the tooling, encoding the constraints, and measuring whether the constraints are holding.

---

## The Ownership Argument

The manager who built an app in a day is right about what they did. The question is what it costs to own that app next month, and the month after.

Unstructured AI use is expensive and gets more expensive over time. Every call re-derives what was derived before. Every change touches things it shouldn't because the architecture wasn't designed to isolate it. Every incident costs more to diagnose because the logs weren't designed for forensics.

Structured AI use — compiled inference, feedback loops, constrained tooling — gets cheaper over time. Each pass through the loop encodes something that doesn't need to be re-derived. Each layer of architecture narrows the blast radius of change. Each feedback cycle makes the next one more precise.

The expertise that builds that structure is quality engineering expertise. Not because QE owns AI — it doesn't — but because cost of ownership thinking, change isolation, and meaningful verification are the core of what quality engineering has always done. AI didn't change the problem. It made the problem arrive faster.

The organizations that figure this out will have AI that gets better and cheaper every quarter. The ones that don't will keep paying inference rates for reasoning they've already done.

---

*For the detailed technical implementation behind these patterns, see:*
*Deterministic AI Development: Levers — A layer on top of skills*
