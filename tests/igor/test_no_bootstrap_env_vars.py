"""Phase 6 regression: paths and config work without bootstrap env vars.

Completion criterion: Igor starts without IGOR_RUNTIME_ROOT or
IGOR_INSTANCE_ID in environment. These tests verify the fallback chain
in paths.py and config.py uses hardcoded defaults when neither var is set.
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class TestPathsDefaultsWithoutEnvVars(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        for key in ("IGOR_RUNTIME_ROOT", "IGOR_INSTANCE_ID"):
            os.environ.pop(key, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_runtime_root_defaults_to_unseen_university(self):
        from unseen_university.devices.igor.paths import _BootstrapPathManager

        pm = _BootstrapPathManager()
        pm._init()
        expected = Path.home() / ".unseen_university"
        self.assertEqual(pm.runtime, expected)

    def test_instance_id_defaults_to_igor_wild_0001(self):
        from unseen_university.devices.igor.paths import _BootstrapPathManager

        pm = _BootstrapPathManager()
        pm._init()
        self.assertEqual(pm.instance_id, "Igor-Wild1")

    def test_instance_dir_resolves_correctly(self):
        from unseen_university.devices.igor.paths import _BootstrapPathManager

        pm = _BootstrapPathManager()
        pm._init()
        expected = Path.home() / ".unseen_university" / "Igor-Wild1"
        self.assertEqual(pm.instance, expected)

    def test_env_override_still_respected_when_set(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            os.environ["IGOR_RUNTIME_ROOT"] = str(tmp_path)
            os.environ["IGOR_INSTANCE_ID"] = "Igor-custom-9999"
            from unseen_university.devices.igor.paths import _BootstrapPathManager

            pm = _BootstrapPathManager()
            pm._init()
            self.assertEqual(pm.runtime, tmp_path.resolve())
            self.assertEqual(pm.instance_id, "Igor-custom-9999")


class TestConfigDefaultsWithoutEnvVars(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        for key in ("IGOR_RUNTIME_ROOT", "IGOR_INSTANCE_ID"):
            os.environ.pop(key, None)
        # Force config to reload after env change
        import unseen_university.devices.igor.config as _cfg

        _cfg._loaded = False
        _cfg._cfg_cache.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        import unseen_university.devices.igor.config as _cfg

        _cfg._loaded = False
        _cfg._cfg_cache.clear()

    def test_config_get_returns_default_when_key_absent(self):
        from unseen_university.devices.igor.config import get

        result = get("SOME_KEY_THAT_DOES_NOT_EXIST", "my_default")
        self.assertEqual(result, "my_default")

    def test_config_load_does_not_raise_without_bootstrap_vars(self):
        from unseen_university.devices.igor.config import _load_cfg_files

        try:
            _load_cfg_files()
        except Exception as exc:
            self.fail(f"_load_cfg_files() raised without bootstrap vars: {exc}")

    def test_config_get_bool_returns_default(self):
        from unseen_university.devices.igor.config import get_bool

        self.assertFalse(get_bool("NONEXISTENT_FLAG"))
        self.assertTrue(get_bool("NONEXISTENT_FLAG", True))


if __name__ == "__main__":
    unittest.main()
