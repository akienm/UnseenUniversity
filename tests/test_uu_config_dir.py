"""Proof test for T-canonical-config-dir-resolver.

Behavioral claim: ``uu_config_dir()`` resolves to the real bundled-config
directory inside the package (``unseen_university/config``) — depth-independent,
not a fragile ``parents[N]`` count — and that directory contains the known
config assets every call-site reads (profiles/, policies/, audit_checks/).

A hollow stub (returning a wrong/nonexistent path) fails the value assertions
below — that is the authentic red the proof-on-close gate requires.
"""
from __future__ import annotations

from pathlib import Path

import unseen_university
from unseen_university._uu_root import uu_config_dir


def test_uu_config_dir_resolves_to_packaged_config():
    cfg = uu_config_dir()

    # Anchored on the installed package, not the caller's cwd or a parents[N] count.
    pkg_root = Path(unseen_university.__file__).resolve().parent
    assert cfg == pkg_root / "config", (
        f"uu_config_dir() must resolve to <package>/config; got {cfg}"
    )

    # The real directory exists and holds the assets the call-sites read.
    assert cfg.is_dir(), f"config dir does not exist: {cfg}"
    assert (cfg / "profiles").is_dir(), "expected profiles/ under config dir"
    assert (cfg / "policies").is_dir(), "expected policies/ under config dir"
    assert (cfg / "audit_checks").is_dir(), "expected audit_checks/ under config dir"

    # The same path the old parents[N] idioms targeted (regression anchor):
    # a known profile must be readable from it.
    assert (cfg / "profiles" / "base.yaml").is_file(), (
        "base.yaml unreadable from uu_config_dir() — resolver points at the "
        "wrong directory"
    )
