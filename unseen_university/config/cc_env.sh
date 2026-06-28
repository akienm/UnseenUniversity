#!/usr/bin/env bash
# intent: shell script: cc_env.sh
# CC session hygiene env vars — source this from ~/.bashrc on each machine.
# D-cc-session-hygiene-2026-05-10: reduce context flooding in long agentic sessions.
#
# Usage: add to ~/.bashrc:
#   source ~/dev/src/UnseenUniversity/config/cc_env.sh

# Compact at 50% context usage (default is 95% — too late for long sprint sessions)
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=50

# Cap MCP tool response size — prevents noisy MCP servers from flooding context
export MAX_MCP_OUTPUT_TOKENS=8000

# Cap bash output forwarded to CC
export BASH_MAX_OUTPUT_LENGTH=20000
