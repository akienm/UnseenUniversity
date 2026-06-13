"""Test that machine_manager fails fast when UU_HOME_DB_URL is not set."""

import importlib
import os
import sys
import unittest


class TestDbUrlGuard(unittest.TestCase):
    def test_missing_db_url_raises(self):
        """machine_manager raises RuntimeError when DB is touched without env.

        T-machine-manager-lazy-db-url-check (2026-04-23): the guard now
        fires at first connect, not at import time. Test updated to
        match — import must succeed; calling _pg_connect must raise.
        """
        saved = os.environ.pop("UU_HOME_DB_URL", None)
        uc_mod_name = "devices.igor.tools.machine_manager"
        saved_uc_mod = sys.modules.pop(uc_mod_name, None)
        try:
            mod = importlib.import_module(uc_mod_name)
            with self.assertRaises(RuntimeError) as ctx:
                mod._pg_connect()
            self.assertIn("UU_HOME_DB_URL not set", str(ctx.exception))
        finally:
            if saved is not None:
                os.environ["UU_HOME_DB_URL"] = saved
            if saved_uc_mod is not None:
                sys.modules[uc_mod_name] = saved_uc_mod

    def test_db_url_present_no_error(self):
        """machine_manager imports cleanly when UU_HOME_DB_URL is set."""
        os.environ.setdefault("UU_HOME_DB_URL", "postgresql://test@localhost/test")
        mod_name = "devices.igor.tools.machine_manager"
        saved_mod = sys.modules.pop(mod_name, None)
        try:
            mod = importlib.import_module(mod_name)
            self.assertTrue(hasattr(mod, "get_ranked_machines"))
        finally:
            if saved_mod is not None:
                sys.modules[mod_name] = saved_mod


if __name__ == "__main__":
    unittest.main()
