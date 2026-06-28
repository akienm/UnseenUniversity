"""
Reference chat agent — minimal example of building on unseen_university.

Demonstrates:
- Direct Anthropic SDK usage (no frameworks)
- Optional memory persistence via unseen_university.db
- Clean shutdown on 'quit', 'exit', or Ctrl+C

Usage:
    python agent.py
    python agent.py --no-memory
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"
SYSTEM_PROMPT = "You are a helpful assistant. Be concise."
MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Optional persistence
# ---------------------------------------------------------------------------


def _try_init_db():
    """
    Return a PGDatabaseProxy if AGENT_DATACENTER_DB_URL is configured, else None.
    Fails silently — memory is optional.
    """
    try:
        from unseen_university.db_proxy import make_dc_proxy

        proxy = make_dc_proxy()
        # Smoke-test the connection.
        with proxy() as conn:
            conn.execute("SELECT 1")
        log.info("DB connected — turn logging enabled")
        return proxy
    except RuntimeError as exc:
        log.debug("DB unavailable (not configured): %s", exc)
        return None
    except Exception as exc:
        log.warning("DB connection failed, running without memory: %s", exc)
        return None


def _log_turn(proxy, role: str, content: str) -> None:
    """
    Persist a conversation turn.  Expects a 'chat_log' table:

        CREATE TABLE chat_log (
            id      SERIAL PRIMARY KEY,
            role    TEXT NOT NULL,
            content TEXT NOT NULL,
            ts      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

    No-ops if proxy is None or the table doesn't exist.
    """
    if proxy is None:
        return
    try:
        with proxy() as conn:
            conn.execute(
                "INSERT INTO chat_log (role, content) VALUES (%s, %s)",
                (role, content),
            )
    except Exception as exc:
        log.debug("Could not log turn: %s", exc)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def _build_client():
    """Return an Anthropic client. Exits early with a clear message if SDK missing."""
    try:
        import anthropic
    except ImportError:
        print(
            "ERROR: anthropic SDK not installed. Run: pip install anthropic",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    return anthropic.Anthropic(api_key=api_key)


def chat_loop(proxy) -> None:
    import anthropic

    client = _build_client()
    history: list[dict] = []

    print("Chat agent ready. Type 'quit' or 'exit' to stop, Ctrl+C to abort.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit"}:
            print("Bye.")
            break

        history.append({"role": "user", "content": user_input})
        _log_turn(proxy, "user", user_input)

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=history,
            )
        except anthropic.APIError as exc:
            print(f"API error: {exc}", file=sys.stderr)
            history.pop()  # don't add unanswered turn to history
            continue

        reply = response.content[0].text
        history.append({"role": "assistant", "content": reply})
        _log_turn(proxy, "assistant", reply)

        print(f"Agent: {reply}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal reference chat agent.")
    parser.add_argument("--no-memory", action="store_true", help="Skip DB persistence.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    proxy = None if args.no_memory else _try_init_db()
    chat_loop(proxy)


if __name__ == "__main__":
    main()
