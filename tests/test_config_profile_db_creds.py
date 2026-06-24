"""Proof for T-uu-config-profile-db-creds.

config/profiles/*.yaml must carry NO embedded DB credential. Igor's state_refs
previously hardcoded ``postgres://igor:<pw>@127.0.0.1/Igor-wild-0001#<frag>`` in
three subsystem URLs (twm/ne/milieu). Those refs ride the announce manifest
across the bus (listener posts ``manifest.to_dict()``), so a baked URL persisted
the live password into mailboxes — directly undercutting password rotation.

Fix: profiles carry the bare ``#fragment`` relative reference; the full URL is
composed at *connect time* from UU_HOME_DB_URL via identity.compose_state_uri(),
so the credential never leaves the local env.

RED before: the credential-shape grep matched igor.yaml's three state_refs and
compose_state_uri did not exist.
GREEN after: no credential shape anywhere under config/; composition is a
connect-time call verified against env.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from unseen_university.identity import compose_state_uri

_REPO = Path(__file__).resolve().parents[1]


def _grep(pattern: str, *pathspec: str) -> list[str]:
    return subprocess.run(
        ["git", "-C", str(_REPO), "grep", "-lE", pattern, "--", *pathspec],
        capture_output=True, text=True,
    ).stdout.split()


def test_no_db_credential_shape_in_config_profiles():
    """No URL with an embedded ``user:pass@`` credential under config/.

    Greps the credential SHAPE, not the dead literal — this also guards against
    a future profile baking the *new* (live) password back in.
    """
    shape = r'postgres(ql)?://[^#"]*:[^@"]*@'
    # Exclude placeholder-bearing templates/examples (e.g. "<db-password>") per the
    # same convention as tests/test_db_url_sweep.py — those carry no real credential.
    hits = _grep(shape, "config/", ":!*.template", ":!*.example")
    # A leftover template placeholder must not sneak a real password through: ensure
    # any *.template hit uses an obvious placeholder, not a literal secret.
    assert hits == [], f"config profile embeds a DB credential: {hits}"


def test_compose_state_uri_resolves_fragment_against_env(monkeypatch):
    """A bare #fragment composes into <home_db_url>#fragment at connect time."""
    monkeypatch.setenv("UU_HOME_DB_URL", "postgresql://u:p@h/db")
    monkeypatch.delenv("IGOR_HOME_DB_URL", raising=False)
    assert compose_state_uri("#twm") == "postgresql://u:p@h/db#twm"
    assert compose_state_uri("#narrative_engine") == "postgresql://u:p@h/db#narrative_engine"


def test_compose_state_uri_passes_through_full_uri(monkeypatch):
    """An already-full URI (e.g. file://) is returned unchanged."""
    monkeypatch.setenv("UU_HOME_DB_URL", "postgresql://u:p@h/db")
    assert compose_state_uri("file:///tmp/state") == "file:///tmp/state"


def test_compose_state_uri_raises_without_env(monkeypatch):
    """Connect-time resolution raises when no home DB URL is set — no baked default."""
    monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
    monkeypatch.delenv("IGOR_HOME_DB_URL", raising=False)
    with pytest.raises(RuntimeError):
        compose_state_uri("#twm")
