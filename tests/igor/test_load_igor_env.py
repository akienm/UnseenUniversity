"""
Regression test for T-cert-debugger-env-mirror —
load_igor_env_into_environ() helper + pe_chain_debugger.start integration.

The cert harness silently routed every HYPOTHESIZE call through local Ollama
qwen2.5:7b instead of cloud OR qwen-2.5-coder-32b for 7+ attempts because
/tmp/run_walk_02.py never set IGOR_CLOUD_PROGRAMMING=true. This helper makes
standalone tools mirror Igor's runtime env without reaching the DB.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from wild_igor.igor.env_sync import load_igor_env_into_environ


class TestLoadIgorEnvIntoEnviron(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def _make_instance_dir(self, root: Path, name: str = "Igor-test-0001") -> Path:
        d = root / name
        d.mkdir(parents=True)
        return d

    def test_loads_switches_cfg_into_environ(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inst = self._make_instance_dir(tmp_path)
            (inst / "igor.switches.cfg").write_text(
                "IGOR_CLOUD_PROGRAMMING=true\nIGOR_TURN_PIPELINE=false\n"
            )
            with patch("pathlib.Path.home", return_value=tmp_path.parent):
                tmp_path.rename(tmp_path.parent / ".TheIgors")
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


class TestPeChainDebuggerLoadsEnv(unittest.TestCase):
    """Integration check: pe_chain_debugger.start invokes the helper."""

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_start_calls_load_igor_env_into_environ(self):
        from wild_igor.igor.tools import pe_chain_debugger

        with patch("wild_igor.igor.env_sync.load_igor_env_into_environ") as mock_load:
            mock_load.return_value = {"IGOR_CLOUD_PROGRAMMING": "true"}
            # Call with an unknown breakpoint so start() returns before
            # actually running pe_chain — we only need to assert the load
            # call landed.
            result = pe_chain_debugger.start(
                ticket_id="T-noop", breakpoint="UNKNOWN_BP"
            )
        self.assertFalse(result["ok"])
        mock_load.assert_called_once()


if __name__ == "__main__":
    unittest.main()
