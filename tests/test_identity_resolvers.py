"""Proof for the canonical identity resolvers (T-uu-identity-resolvers).

The contract under test: call-time resolution, raise-if-unset for required identity,
and — the load-bearing one — IMPORT-SAFETY (importing the module with every identity
var unset must not read env or raise). That import-safety is the lazy-vs-eager
discriminator the whole config-identity layer hinges on: a module-scope binding would
crash ~91 importers when env is unset and break the recovery contract.

The proof test (`test_igor_name_resolves_via_subprocess`) runs the resolver in a
SUBPROCESS so that the pre-implementation state (module absent) yields an
AssertionError on the captured output rather than an ImportError — the proof harness
rejects missing-module red as inauthentic.
"""
import os
import socket
import subprocess
import sys

import pytest


def _base_env(**extra):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.environ.get("HOME", "/tmp")}
    env.update(extra)
    return env


def test_import_is_side_effect_free_with_all_identity_unset():
    # Clean env: none of IGOR_NAME / UU_HOME_DB_URL / IGOR_SWARM_NAME present.
    # Importing must still succeed (no module-scope binding, no env read at import).
    r = subprocess.run(
        [sys.executable, "-c", "import unseen_university.identity"],
        env=_base_env(PYTHONPATH=os.getcwd()),
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr


def test_igor_name_resolves_via_subprocess():
    # PROOF test: subprocess so module-absent red is an AssertionError, not ImportError.
    r = subprocess.run(
        [sys.executable, "-c",
         "from unseen_university import identity; print(identity.igor_name(), end='')"],
        env=_base_env(PYTHONPATH=os.getcwd(), IGOR_NAME="Igor-wild-0001"),
        capture_output=True, text=True, timeout=30,
    )
    assert r.stdout == "Igor-wild-0001"


def test_igor_name_raises_when_unset(monkeypatch):
    from unseen_university import identity
    monkeypatch.delenv("IGOR_NAME", raising=False)
    with pytest.raises(RuntimeError):
        identity.igor_name()


def test_home_db_url_raises_when_unset(monkeypatch):
    from unseen_university import identity
    monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
    monkeypatch.delenv("IGOR_HOME_DB_URL", raising=False)
    with pytest.raises(RuntimeError):
        identity.home_db_url()


def test_home_db_url_returns_env(monkeypatch):
    from unseen_university import identity
    monkeypatch.setenv("UU_HOME_DB_URL", "postgresql://u:p@h/db")
    assert identity.home_db_url() == "postgresql://u:p@h/db"


def test_swarm_hostname_prefers_env_then_hostname(monkeypatch):
    from unseen_university import identity
    monkeypatch.setenv("IGOR_SWARM_NAME", "swarm-x")
    assert identity.swarm_hostname() == "swarm-x"
    monkeypatch.delenv("IGOR_SWARM_NAME", raising=False)
    assert identity.swarm_hostname() == socket.gethostname()
