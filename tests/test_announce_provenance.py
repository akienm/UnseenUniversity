"""
ProvenanceService tests — covers all three completion criteria:

  1. Announcing agent receives a token in its manifest.
  2. Registry JSON contains the token.
  3. A second announce with the same agent_id but no valid prior token gets a
     fresh token (not reuse).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from unseen_university.announce.broker import AnnounceBroker
from unseen_university.announce.envelope import IdentityEnvelope
from unseen_university.announce.provenance import ProvenanceService

CANONICAL_PROFILES = Path(__file__).parent.parent / "config" / "profiles"


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_provenance(tmp_path: Path) -> ProvenanceService:
    return ProvenanceService(
        registry_dir=tmp_path / "registry",
        secret_path=tmp_path / "rack.secret",
    )


@pytest.fixture()
def profiles_dir(tmp_path: Path) -> Path:
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", tmp_path / "igor.yaml")
    return tmp_path


@pytest.fixture()
def igor_envelope() -> IdentityEnvelope:
    return IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testbox",
        box_n=0,
        pid=9999,
        interface_version="1.0",
    )


# ── Completion criterion 1: manifest includes a non-empty token ───────────────


def test_manifest_includes_token(
    profiles_dir: Path, igor_envelope: IdentityEnvelope, tmp_path: Path
) -> None:
    prov = _make_provenance(tmp_path)
    broker = AnnounceBroker(profiles_dir=profiles_dir, provenance=prov)
    manifest = broker.resolve_announce(igor_envelope)

    assert manifest.token is not None
    assert isinstance(manifest.token, str)
    assert len(manifest.token) > 0


def test_manifest_token_in_to_dict(
    profiles_dir: Path, igor_envelope: IdentityEnvelope, tmp_path: Path
) -> None:
    prov = _make_provenance(tmp_path)
    broker = AnnounceBroker(profiles_dir=profiles_dir, provenance=prov)
    manifest = broker.resolve_announce(igor_envelope)

    d = manifest.to_dict()
    assert "token" in d
    assert d["token"] == manifest.token


# ── Completion criterion 2: registry JSON contains the token ──────────────────


def test_registry_json_written_after_issue(tmp_path: Path) -> None:
    prov = _make_provenance(tmp_path)
    token = prov.issue_token("igor", "wild-0001", time.time())

    tokens_path = tmp_path / "registry" / "tokens.json"
    assert tokens_path.exists()
    store = json.loads(tokens_path.read_text())
    assert "igor" in store
    assert store["igor"]["token"] == token


def test_manifest_token_matches_registry(
    profiles_dir: Path, igor_envelope: IdentityEnvelope, tmp_path: Path
) -> None:
    prov = _make_provenance(tmp_path)
    broker = AnnounceBroker(profiles_dir=profiles_dir, provenance=prov)
    manifest = broker.resolve_announce(igor_envelope)

    store = json.loads((tmp_path / "registry" / "tokens.json").read_text())
    assert store["igor"]["token"] == manifest.token


# ── Completion criterion 3: no valid prior token → fresh token (not reuse) ────


def test_no_valid_prior_token_gets_fresh_token(tmp_path: Path) -> None:
    prov = _make_provenance(tmp_path)
    token1 = prov.issue_token("igor", "wild-0001", time.time())
    # Simulate rack restart — all tokens expire.
    prov.clear_all()
    # New session, different instance: no valid prior token → fresh token.
    token2 = prov.issue_token("igor", "wild-0002", time.time())
    assert token2 != token1


def test_different_instance_gets_fresh_token(tmp_path: Path) -> None:
    prov = _make_provenance(tmp_path)
    token1 = prov.issue_token("igor", "wild-0001", time.time())
    # Same agent_id, different instance (prior token invalidated between sessions).
    prov.invalidate("igor")
    token2 = prov.issue_token("igor", "wild-0002", time.time())
    assert token2 != token1


# ── Reuse within same session ─────────────────────────────────────────────────


def test_same_session_reannounce_reuses_token(tmp_path: Path) -> None:
    prov = _make_provenance(tmp_path)
    ts = time.time()
    token1 = prov.issue_token("igor", "wild-0001", ts)
    token2 = prov.issue_token(
        "igor", "wild-0001", ts + 5.0
    )  # re-announce, same instance
    assert token1 == token2


# ── Verify and invalidate ─────────────────────────────────────────────────────


def test_verify_live_token(tmp_path: Path) -> None:
    prov = _make_provenance(tmp_path)
    token = prov.issue_token("cc", "cc-0001", time.time())
    assert prov.verify(token) is True


def test_verify_returns_false_after_invalidate(tmp_path: Path) -> None:
    prov = _make_provenance(tmp_path)
    token = prov.issue_token("cc", "cc-0001", time.time())
    prov.invalidate("cc")
    assert prov.verify(token) is False


def test_verify_returns_false_after_clear_all(tmp_path: Path) -> None:
    prov = _make_provenance(tmp_path)
    token = prov.issue_token("igor", "wild-0001", time.time())
    prov.clear_all()
    assert prov.verify(token) is False


# ── No provenance → token is None ────────────────────────────────────────────


def test_no_provenance_token_is_none(
    profiles_dir: Path, igor_envelope: IdentityEnvelope
) -> None:
    broker = AnnounceBroker(profiles_dir=profiles_dir)
    manifest = broker.resolve_announce(igor_envelope)
    assert manifest.token is None


# ── Token payload is base64url-encoded JSON ───────────────────────────────────


def test_token_decodes_to_expected_fields(tmp_path: Path) -> None:
    import base64

    prov = _make_provenance(tmp_path)
    token = prov.issue_token("igor", "wild-0001", time.time())
    payload = json.loads(base64.urlsafe_b64decode(token + "=="))
    assert payload["agent_id"] == "igor"
    assert payload["instance"] == "wild-0001"
    assert "announce_timestamp" in payload
    assert "rack_signature" in payload
