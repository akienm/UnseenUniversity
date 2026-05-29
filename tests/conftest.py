"""
Shared pytest configuration for the tests/ directory.

Sets AGENT_DATACENTER_TEST_MODE=1 before any test module is imported so that
bus.imap_server._TEST_MODE evaluates to True in every test that touches the
bus. This avoids the ordering hazard where a test file that doesn't set the
var caches the module in production mode before stub-reliant tests run.
"""

import os

os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")
