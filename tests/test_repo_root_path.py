"""Tests for uu bootstrap --path (idempotent PATH install)."""

from __future__ import annotations

import pathlib
import types
from pathlib import Path


_UU_ROOT = Path(__file__).resolve().parents[1]


def _load_uu_mod():
    """Execute the uu script in an isolated namespace and return it as a module."""
    src = (_UU_ROOT / "uu").read_text()
    ns = {"__file__": str(_UU_ROOT / "uu"), "__name__": "uu_script"}
    exec(compile(src, str(_UU_ROOT / "uu"), "exec"), ns)
    mod = types.SimpleNamespace(**ns)
    return mod


class TestInstallPath:
    def _call(self, mod, tmp_path):
        orig = pathlib.Path.home
        pathlib.Path.home = classmethod(lambda cls: tmp_path)
        try:
            mod._install_path()
        finally:
            pathlib.Path.home = orig

    def test_adds_uu_root_to_bashrc(self, tmp_path):
        mod = _load_uu_mod()
        (tmp_path / ".bashrc").write_text("# existing\n")
        self._call(mod, tmp_path)
        assert str(mod.UU_ROOT) in (tmp_path / ".bashrc").read_text()

    def test_idempotent_no_duplicate(self, tmp_path):
        mod = _load_uu_mod()
        (tmp_path / ".bashrc").write_text("# existing\n")
        self._call(mod, tmp_path)
        self._call(mod, tmp_path)
        content = (tmp_path / ".bashrc").read_text()
        assert content.count(str(mod.UU_ROOT)) == 1

    def test_updates_zshrc_when_present(self, tmp_path):
        mod = _load_uu_mod()
        (tmp_path / ".bashrc").write_text("# bash\n")
        (tmp_path / ".zshrc").write_text("# zsh\n")
        self._call(mod, tmp_path)
        assert str(mod.UU_ROOT) in (tmp_path / ".zshrc").read_text()

    def test_skips_absent_zshrc(self, tmp_path):
        mod = _load_uu_mod()
        (tmp_path / ".bashrc").write_text("# bash\n")
        self._call(mod, tmp_path)
        assert not (tmp_path / ".zshrc").exists()
        assert str(mod.UU_ROOT) in (tmp_path / ".bashrc").read_text()
