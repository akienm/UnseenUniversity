"""
Bus connection factory.

Returns a PgBus connected to the Postgres message bus. The bus schema
(bus.mailboxes, bus.messages) is created on first start() call.

Callers receive a started bus ready for append/fetch_unseen/idle_wait.
"""


def make_bus_connection():
    """Return a PgBus ready for agent bus operations."""
    from .pg_bus import PgBus

    bus = PgBus()
    bus.start()
    return bus
