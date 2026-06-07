"""Tests for unseen_university.notify — NotificationConfig and DeliveryMode."""

from __future__ import annotations

import pytest

from unseen_university.notify import DeliveryMode, NotificationConfig, _CONFIG_FILENAME


class TestDeliveryMode:
    def test_values_exist(self):
        assert DeliveryMode.SILENT.value == "SILENT"
        assert DeliveryMode.QUIET.value == "QUIET"
        assert DeliveryMode.LOUD.value == "LOUD"

    def test_case_insensitive_construction(self):
        assert DeliveryMode("quiet") == DeliveryMode.QUIET
        assert DeliveryMode("LOUD") == DeliveryMode.LOUD
        assert DeliveryMode("silent") == DeliveryMode.SILENT

    def test_unknown_value_falls_back_to_quiet(self):
        assert DeliveryMode("nonsense") == DeliveryMode.QUIET


class TestNotificationConfigLoad:
    def test_load_missing_file_creates_default_and_returns_quiet(self, tmp_path):
        cfg = NotificationConfig.load(tmp_path)
        assert cfg.default_level == DeliveryMode.QUIET
        assert (tmp_path / _CONFIG_FILENAME).exists()

    def test_load_written_config(self, tmp_path):
        (tmp_path / _CONFIG_FILENAME).write_text(
            "[defaults]\nlevel = LOUD\n\n[overrides]\nakien = LOUD\ngranny-weatherwax = QUIET\n",
            encoding="utf-8",
        )
        cfg = NotificationConfig.load(tmp_path)
        assert cfg.default_level == DeliveryMode.LOUD
        assert cfg.overrides["akien"] == DeliveryMode.LOUD
        assert cfg.overrides["granny-weatherwax"] == DeliveryMode.QUIET

    def test_load_corrupted_file_returns_defaults(self, tmp_path):
        (tmp_path / _CONFIG_FILENAME).write_text("NOT INI :::::", encoding="utf-8")
        cfg = NotificationConfig.load(tmp_path)
        assert cfg.default_level == DeliveryMode.QUIET

    def test_load_is_idempotent(self, tmp_path):
        NotificationConfig.load(tmp_path)
        NotificationConfig.load(tmp_path)
        assert len(list(tmp_path.iterdir())) == 1


class TestNotificationConfigGetLevel:
    def test_default_returned_when_no_override(self):
        cfg = NotificationConfig(default_level=DeliveryMode.SILENT)
        assert cfg.get_level("unknown-sender") == DeliveryMode.SILENT

    def test_override_wins_over_default(self):
        cfg = NotificationConfig(
            default_level=DeliveryMode.SILENT,
            overrides={"akien": DeliveryMode.LOUD},
        )
        assert cfg.get_level("akien") == DeliveryMode.LOUD
        assert cfg.get_level("granny-weatherwax") == DeliveryMode.SILENT

    def test_multiple_overrides_independent(self):
        cfg = NotificationConfig(
            default_level=DeliveryMode.QUIET,
            overrides={
                "akien": DeliveryMode.LOUD,
                "igor": DeliveryMode.SILENT,
            },
        )
        assert cfg.get_level("akien") == DeliveryMode.LOUD
        assert cfg.get_level("igor") == DeliveryMode.SILENT
        assert cfg.get_level("librarian") == DeliveryMode.QUIET


class TestWriteDefault:
    def test_creates_file(self, tmp_path):
        NotificationConfig.write_default(tmp_path)
        assert (tmp_path / _CONFIG_FILENAME).exists()

    def test_creates_readable_config(self, tmp_path):
        NotificationConfig.write_default(tmp_path)
        cfg = NotificationConfig.load(tmp_path)
        assert cfg.default_level == DeliveryMode.QUIET

    def test_idempotent(self, tmp_path):
        NotificationConfig.write_default(tmp_path)
        NotificationConfig.write_default(tmp_path)
        files = list(tmp_path.iterdir())
        assert len(files) == 1

    def test_creates_parent_dirs(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        NotificationConfig.write_default(deep)
        assert (deep / _CONFIG_FILENAME).exists()


class TestSave:
    def test_save_roundtrip(self, tmp_path):
        cfg = NotificationConfig(
            default_level=DeliveryMode.LOUD,
            overrides={"akien": DeliveryMode.LOUD, "granny-weatherwax": DeliveryMode.SILENT},
        )
        cfg.save(tmp_path)
        loaded = NotificationConfig.load(tmp_path)
        assert loaded.default_level == DeliveryMode.LOUD
        assert loaded.overrides["akien"] == DeliveryMode.LOUD
        assert loaded.overrides["granny-weatherwax"] == DeliveryMode.SILENT

    def test_save_no_overrides(self, tmp_path):
        cfg = NotificationConfig(default_level=DeliveryMode.SILENT)
        cfg.save(tmp_path)
        loaded = NotificationConfig.load(tmp_path)
        assert loaded.default_level == DeliveryMode.SILENT
        assert loaded.overrides == {}
