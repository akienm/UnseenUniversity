"""
Tests for load_igor_env_into_environ() helper.

The helper makes standalone tools mirror Igor's runtime env without reaching the DB.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from devices.igor.env_sync import load_igor_env_into_environ


class TestLoadIgorEnvIntoEnviron(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        # Clear all IGOR_* vars so tests don't inherit the live runner's env.
        # Igor's main loop loads IGOR_CLOUD_PROGRAMMING=true at startup, which
        # causes load_igor_env_into_environ(overwrite=False) to skip applying
        # the cfg file value, making assertIn("IGOR_CLOUD_PROGRAMMING", applied)
        # fail. tearDown restores the full saved env.
        for key in list(os.environ):
            if key.startswith("IGOR_"):
                del os.environ[key]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_loads_switches_cfg_into_environ(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inst = tmp_path / ".TheIgors" / "Igor-test-0001"
            inst.mkdir(parents=True)
            (inst / "igor.switches.cfg").write_text(
                "IGOR_CLOUD_PROGRAMMING=true\nIGOR_TURN_PIPELINE=false\n"
            )
            with patch("pathlib.Path.home", return_value=tmp_path):
                applied = load_igor_env_into_environ(instance_id="Igor-test-0001")
        self.assertEqual(os.environ.get("IGOR_CLOUD_PROGRAMMING"), "true")
        self.assertEqual(os.environ.get("IGOR_TURN_PIPELINE"), "false")
        self.assertIn("IGOR_CLOUD_PROGRAMMING", applied)

    def test_returns_empty_when_instance_dir_missing(self):
        with TemporaryDirectory() as tmp:
            with patch("pathlib.Path.home", return_value=Path(tmp)):
                applied = load_igor_env_into_environ(instance_id="Igor-nope-9999")
        self.assertEqual(applied, {})

    def test_returns_empty_when_no_cfg_files_present(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inst = tmp_path / ".TheIgors" / "Igor-test-0002"
            inst.mkdir(parents=True)
            with patch("pathlib.Path.home", return_value=tmp_path):
                applied = load_igor_env_into_environ(instance_id="Igor-test-0002")
        self.assertEqual(applied, {})

    def test_default_does_not_overwrite_existing_environ(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inst = tmp_path / ".TheIgors" / "Igor-test-0003"
            inst.mkdir(parents=True)
            (inst / "igor.switches.cfg").write_text("IGOR_CLOUD_PROGRAMMING=false\n")
            os.environ["IGOR_CLOUD_PROGRAMMING"] = "true"  # pre-existing
            with patch("pathlib.Path.home", return_value=tmp_path):
                applied = load_igor_env_into_environ(instance_id="Igor-test-0003")
        # Pre-existing env wins (matches boot_env_sync hydration priority).
        self.assertEqual(os.environ.get("IGOR_CLOUD_PROGRAMMING"), "true")
        self.assertNotIn("IGOR_CLOUD_PROGRAMMING", applied)

    def test_overwrite_true_replaces_existing_environ(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inst = tmp_path / ".TheIgors" / "Igor-test-0004"
            inst.mkdir(parents=True)
            (inst / "igor.switches.cfg").write_text("IGOR_CLOUD_PROGRAMMING=false\n")
            os.environ["IGOR_CLOUD_PROGRAMMING"] = "true"
            with patch("pathlib.Path.home", return_value=tmp_path):
                applied = load_igor_env_into_environ(
                    instance_id="Igor-test-0004", overwrite=True
                )
        self.assertEqual(os.environ.get("IGOR_CLOUD_PROGRAMMING"), "false")
        self.assertEqual(applied.get("IGOR_CLOUD_PROGRAMMING"), "false")

    def test_resolves_instance_id_from_environ_when_not_passed(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inst = tmp_path / ".TheIgors" / "Igor-test-0005"
            inst.mkdir(parents=True)
            (inst / "igor.switches.cfg").write_text("IGOR_CLOUD_PROGRAMMING=true\n")
            os.environ["IGOR_INSTANCE_ID"] = "Igor-test-0005"
            with patch("pathlib.Path.home", return_value=tmp_path):
                applied = load_igor_env_into_environ()
        self.assertIn("IGOR_CLOUD_PROGRAMMING", applied)


if __name__ == "__main__":
    unittest.main()
