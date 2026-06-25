"""Canonical call-time resolvers for install identity.

This is the single import target for de-hardcoding the install's identity across the
repo: the instance / home-DB name (was 'Igor-wild-0001'), the home DB URL, and the
machine/swarm name (previously a hardcoded host literal). The three sweep tickets point their files
here.

Contract (the lazy-vs-eager discriminator):
  * Every value resolves at CALL time from the environment — there is NO module-scope
    binding and NO baked-in credential fallback.
  * Importing this module must therefore never read the environment or raise, so it is
    safe to import at import time anywhere. The rescueclaude recovery contract (a shell
    that boots even with nothing of ours present) and clean pytest collection both
    depend on that import-safety.
  * Required identity (name, DB URL) raises RuntimeError when unset rather than falling
    back to a baked-in value (CP6 — credential hygiene). The hostname resolver is total
    (the hostname is always discoverable) so it never raises.

Mirrors the established, blessed precedent: unseen_university.db_proxy.make_home_proxy
and devices/igor/paths.Paths.home_db_url already resolve this way. Those callers will
delegate here in the sweep tickets; this module gives them one place to call.
(D-uu-config-identity-layer-2026-06-22 — T-uu-identity-resolvers.)
"""
from __future__ import annotations

import os
import socket


def igor_name() -> str:
    """This instance / home-DB name, from env ``IGOR_NAME``. Raises if unset.

    The current value is ``Igor-wild-0001`` — but it lives in config
    (~/.unseen_university/uu_bash_profile.sh), not in code. The later rename ticket
    changes the value; this resolver makes that a one-line config edit.
    """
    name = os.environ.get("IGOR_NAME")
    if not name:
        raise RuntimeError(
            "IGOR_NAME not set — export the instance / home-DB name (e.g. "
            "Igor-wild-0001). It is set by ~/.unseen_university/uu_bash_profile.sh."
        )
    return name


def home_db_url() -> str:
    """Postgres URL for this instance's home DB, from env ``UU_HOME_DB_URL``
    (``IGOR_HOME_DB_URL`` accepted for legacy callers). Raises if unset.

    Never returns a baked-in credential — an unset URL is a configuration error the
    caller must fix, not something to paper over with a default password.
    """
    url = os.environ.get("UU_HOME_DB_URL") or os.environ.get("IGOR_HOME_DB_URL")
    if not url:
        raise RuntimeError(
            "UU_HOME_DB_URL not set — export the Postgres connection string for this "
            "instance (e.g. postgresql://<user>:<password>@<host>/<instance-db>). It "
            "is composed by ~/.unseen_university/uu_bash_profile.sh."
        )
    return url


def compose_state_uri(ref: str) -> str:
    """Resolve a state-ref into a connectable URI at *connect time*.

    Profiles carry state refs as bare ``#fragment`` relative references (e.g.
    ``#twm``) rather than full ``postgres://user:pass@host/db#twm`` URLs. The
    fragment rides the announce manifest (which is serialized and posted across
    the bus); the live credential must NOT — composing here, only when a caller
    actually needs to connect, keeps the password in the local env and out of
    every transmitted/persisted manifest. Raises (via :func:`home_db_url`) when
    ``UU_HOME_DB_URL`` is unset, which is correct at connect time — you cannot
    connect without it, and there is no baked default to paper over the gap.

    An ``ref`` that is already a full URI (contains ``://``, e.g. ``file://``) is
    returned unchanged.
    """
    if "://" in ref:
        return ref
    fragment = ref.lstrip("#")
    return f"{home_db_url()}#{fragment}"


def swarm_hostname() -> str:
    """This machine's swarm name: env ``IGOR_SWARM_NAME`` if set, else the live
    hostname. Total (never raises) — de-hardcodes the previously-baked host literal without adding a
    failure mode, since the hostname is always discoverable.
    """
    return os.environ.get("IGOR_SWARM_NAME") or socket.gethostname()


def instance_id() -> str:
    # STUB (stub-first proof, T-uu-sweep-instance-name) — becomes the IGOR_INSTANCE_ID read in the fix commit.
    return "Igor-wild-0001"
