"""Test that machine_manager fails fast when IGOR_HOME_DB_URL is not set."""

import importlib
import os
import sys
import unittest


class TestDbUrlGuard(unittest.TestCase):
    def test_missing_db_url_raises(self):
        """machine_manager must raise RuntimeError if IGOR_HOME_DB_URL is unset."""
        # Remove from env if present, reload the module
        saved = os.environ.pop("IGOR_HOME_DB_URL", None)
        mod_name = "wild_igor.igor.cognition.machine_manager"
        saved_mod = sys.modules.pop(mod_name, None)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                importlib.import_module(mod_name)
            self.assertIn("IGOR_HOME_DB_URL not set", str(ctx.exception))
        finally:
            # Restore
            if saved is not None:
                os.environ["IGOR_HOME_DB_URL"] = saved
            if saved_mod is not None:
                sys.modules[mod_name] = saved_mod

    def test_db_url_present_no_error(self):
        """machine_manager imports cleanly when IGOR_HOME_DB_URL is set."""
        os.environ.setdefault("IGOR_HOME_DB_URL", "postgresql://test@localhost/test")
        mod_name = "wild_igor.igor.cognition.machine_manager"
        saved_mod = sys.modules.pop(mod_name, None)
        try:
            mod = importlib.import_module(mod_name)
            self.assertTrue(hasattr(mod, "get_ranked_machines"))
        finally:
            if saved_mod is not None:
                sys.modules[mod_name] = saved_mod


if __name__ == "__main__":
    unittest.main()
