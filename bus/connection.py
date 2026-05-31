"""
Bus connection factory — keeps IMAPServer construction out of announce/.

Production: creates a DovecotClient connection via start().
Test mode:  returns an unstarted IMAPServer; all operations use the shared
            in-process _STUB_MAILBOXES dict so no stub server is needed.
"""

import os

_TEST_MODE = os.environ.get("AGENT_DATACENTER_TEST_MODE", "") == "1"


def make_bus_connection():
    """Return an IMAPServer ready for agent bus operations."""
    from .imap_server import IMAPServer

    server = IMAPServer()
    if not _TEST_MODE:
        server.start()
    return server
