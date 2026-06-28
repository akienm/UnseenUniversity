# Reference chat agent

This is the canonical minimal example of an agent built on `UnseenUniversity`.
It shows the correct patterns — direct Anthropic SDK, optional Postgres memory
via `UnseenUniversity.db`, and clean exit handling — without any framework glue.
Read it before writing a new agent; it is the design reference, not production code.

## Prerequisites

```
pip install anthropic        # not bundled in UnseenUniversity deps
export ANTHROPIC_API_KEY=sk-...
```

Optional — persist conversation turns to Postgres:

```
export AGENT_DATACENTER_DB_URL=postgresql://user:pass@host/agent-datacenter-0001
```

If `AGENT_DATACENTER_DB_URL` is not set, the agent runs without memory (graceful skip).

## How to run

```
python agent.py              # with memory if DB URL is configured
python agent.py --no-memory  # skip DB entirely
python agent.py --debug      # verbose logging
```

Type `quit` or `exit` to stop cleanly. `Ctrl+C` also exits gracefully.

## Persistence schema (optional)

If you want turn logging, create this table in your datacenter DB first:

```sql
CREATE TABLE chat_log (
    id      SERIAL PRIMARY KEY,
    role    TEXT NOT NULL,
    content TEXT NOT NULL,
    ts      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## How to extend

- **Add tools** — pass a `tools=` list to `client.messages.create()` and dispatch
  on `response.stop_reason == "tool_use"` in the loop. See the Anthropic SDK docs.
- **Add bus messages** — import `bus.envelope.Envelope` and wrap turns as
  `Envelope.now(from_device="chat", to_device="...", payload={...})` before
  sending. See `bus/envelope.py` for the schema contract.
- **Swap the model** — change the `MODEL` constant at the top of `agent.py`.
  Current default is `claude-sonnet-4-5`; swap to `claude-haiku-4-5` for
  faster/cheaper, or `claude-opus-4-5` for heavier reasoning.
