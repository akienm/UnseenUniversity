# Librarian Device — Architecture

Long-running research and memory assistant. Serves MCP tools to CC over stdio,
reads Igor's memory palace, queries the DB, and runs inference for research tasks.

## Key files

| File | Purpose |
|---|---|
| `mcp_server.py` | stdio MCP server — `tools/list` + `tools/call` dispatch |
| `librarian.py` | `Librarian` device class — startup, health, background loops |
| `tools/__init__.py` | Aggregates all tool schemas + dispatch router |
| `tools/budget_tools.py` | `check_openrouter_balance`, `openrouter_burn_rate` MCP tools |
| `tools/memory_tools.py` | `memory_get`, `memory_search`, `memory_list_by_type` |
| `tools/palace_tools.py` | `palace_read`, `palace_write`, `palace_search`, `palace_ls` |
| `tools/db_tools.py` | `db_query` — raw SQL against IGOR_HOME_DB_URL |
| `tools/research_tools.py` | `research` — multi-hop research pipeline |
| `tools/igor_tools.py` | `cc_send`, `channel_read`, `channel_send` — Igor comms |
| `tools/health_tools.py` | `rack_health` — aggregated device heartbeat status |
| `inference.py` | Inference backend selection (OR vs Ollama) for research |
| `research.py` | Research pipeline — query planning, fetch, synthesis |

## Adding a new MCP tool

1. Add `SCHEMAS` list entry + `dispatch(name, args)` function to a `tools/*.py` module
2. Import the module in `tools/__init__.py` and add to `SCHEMAS` concat + dispatch loop
3. The MCP server picks it up automatically — no registration needed

## External interfaces

**MCP tools exposed** (via stdio to CC): see `tools/` for full list.

**Env vars:**
- `IGOR_HOME_DB_URL` — Postgres for memory/palace/DB tools
- `OPENROUTER_API_KEY` — for inference in research pipeline
- `IGOR_UC_PORT` / `IGOR_UC_BASE` — UC registration endpoint

**Related devices:** Inference (research pipeline calls it), Igor (channel + memory tools talk to Igor's DB).
