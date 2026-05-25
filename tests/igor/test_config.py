"""
test_config.py — T-config-arch-finish (#448)
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.config import (  # noqa: E402
    _parse_cfg_file,
    get,
    get_bool,
    get_float,
    get_int,
    reload,
)


@pytest.fixture(autouse=True)
def _reset_config():
    reload()
    yield
    reload()


class TestGet:
    def test_env_var_wins_over_cfg(self):
        with patch.dict(os.environ, {"TEST_CONFIG_KEY": "from_env"}):
            assert get("TEST_CONFIG_KEY", "default") == "from_env"

    def test_returns_default_when_missing(self):
        os.environ.pop("NONEXISTENT_CONFIG_KEY_12345", None)
        assert get("NONEXISTENT_CONFIG_KEY_12345", "fallback") == "fallback"

    def test_empty_default(self):
        os.environ.pop("NONEXISTENT_CONFIG_KEY_12345", None)
        assert get("NONEXISTENT_CONFIG_KEY_12345") == ""


class TestGetBool:
    def test_true_values(self):
        for val in ("true", "True", "1", "yes", "YES"):
            with patch.dict(os.environ, {"TEST_BOOL": val}):
                assert get_bool("TEST_BOOL") is True

    def test_false_values(self):
        for val in ("false", "False", "0", "no", "NO"):
            with patch.dict(os.environ, {"TEST_BOOL": val}):
                assert get_bool("TEST_BOOL") is False

    def test_default_false(self):
        os.environ.pop("NONEXISTENT_BOOL", None)
        assert get_bool("NONEXISTENT_BOOL") is False

    def test_default_true(self):
        os.environ.pop("NONEXISTENT_BOOL", None)
        assert get_bool("NONEXISTENT_BOOL", True) is True


class TestGetInt:
    def test_valid_int(self):
        with patch.dict(os.environ, {"TEST_INT": "42"}):
            assert get_int("TEST_INT") == 42

    def test_invalid_returns_default(self):
        with patch.dict(os.environ, {"TEST_INT": "not_a_number"}):
            assert get_int("TEST_INT", 10) == 10

    def test_missing_returns_default(self):
        os.environ.pop("NONEXISTENT_INT", None)
        assert get_int("NONEXISTENT_INT", 99) == 99


class TestGetFloat:
    def test_valid_float(self):
        with patch.dict(os.environ, {"TEST_FLOAT": "3.14"}):
            assert get_float("TEST_FLOAT") == pytest.approx(3.14)

    def test_invalid_returns_default(self):
        with patch.dict(os.environ, {"TEST_FLOAT": "nope"}):
            assert get_float("TEST_FLOAT", 1.0) == 1.0


class TestParseCfgFile:
    def test_parses_key_value(self, tmp_path):
        cfg = tmp_path / "test.cfg"
        cfg.write_text("FOO=bar\nBAZ=123\n")
        from devices.igor import config

        config._cfg_cache.clear()
        _parse_cfg_file(cfg)
        assert config._cfg_cache["FOO"] == "bar"
        assert config._cfg_cache["BAZ"] == "123"

    def test_skips_comments(self, tmp_path):
        cfg = tmp_path / "test.cfg"
        cfg.write_text("# comment\nKEY=val\n")
        from devices.igor import config

        config._cfg_cache.clear()
        _parse_cfg_file(cfg)
        assert "# comment" not in config._cfg_cache
        assert config._cfg_cache["KEY"] == "val"

    def test_strips_quotes(self, tmp_path):
        cfg = tmp_path / "test.cfg"
        cfg.write_text('QUOTED="hello world"\n')
        from devices.igor import config

        config._cfg_cache.clear()
        _parse_cfg_file(cfg)
        assert config._cfg_cache["QUOTED"] == "hello world"

    def test_handles_missing_file(self, tmp_path):
        _parse_cfg_file(tmp_path / "nonexistent.cfg")


class TestCfgPrecedence:
    def test_cfg_file_value_used_when_env_unset(self, tmp_path):
        from devices.igor import config

        config._cfg_cache["CFG_ONLY_KEY"] = "from_cfg"
        os.environ.pop("CFG_ONLY_KEY", None)
        assert get("CFG_ONLY_KEY") == "from_cfg"

    def test_env_overrides_cfg(self, tmp_path):
        from devices.igor import config

        config._cfg_cache["OVERRIDE_KEY"] = "from_cfg"
        with patch.dict(os.environ, {"OVERRIDE_KEY": "from_env"}):
            assert get("OVERRIDE_KEY") == "from_env"
