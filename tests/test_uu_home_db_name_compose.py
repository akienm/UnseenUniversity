"""Proof for T-uu-rename-role-and-db (the git-tracked part).

The live DB/role rename (Igor-wild-0001/igor -> unseen_university) is a one-shot
DDL op verified at runtime; what's git-tracked and provable is the config-layer
decoupling: the home DB name comes from UU_HOME_DB_NAME (substrate-owned),
*not* from IGOR_NAME (the igor tenant identity / on-disk instance folder, which
stays Igor-wild-0001). IGOR_NAME remains the fallback so installs predating the
rename keep their old behavior.

RED before: bin/uu_bash_profile_processor.sh composed the URL's DB component as
/${IGOR_NAME} unconditionally, so UU_HOME_DB_NAME had no effect.
GREEN after: UU_HOME_DB_NAME drives the DB name; IGOR_NAME is fallback only.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PROC = _REPO / "bin" / "uu_bash_profile_processor.sh"


def _compose(env_extra: dict, tmp_home: Path) -> str:
    """Source the processor in isolation (sandboxed HOME) and return UU_HOME_DB_URL."""
    env = {
        "HOME": str(tmp_home),
        "UU_ROOT": str(_REPO),
        "PATH": "/usr/bin:/bin",
        "UU_DB_USER": "role_x",
        "UU_DB_PASSWORD": "pw_x",
        "UU_DB_IP": "10.9.9.9",
        "IGOR_NAME": "tenant_inst",
    }
    env.update(env_extra)
    r = subprocess.run(
        ["bash", "-c", f'source "{_PROC}"; printf "%s" "${{UU_HOME_DB_URL:-}}"'],
        env=env, capture_output=True, text=True,
    )
    return r.stdout.strip()


def test_db_name_comes_from_uu_home_db_name(tmp_path):
    """The DB component is UU_HOME_DB_NAME, not the IGOR_NAME tenant identity."""
    url = _compose({"UU_HOME_DB_NAME": "substrate_db"}, tmp_path)
    assert url.endswith("/substrate_db"), url
    assert "tenant_inst" not in url, f"DB name leaked the tenant identity: {url}"


def test_db_name_falls_back_to_igor_name(tmp_path):
    """With UU_HOME_DB_NAME unset, behavior is preserved: DB name == IGOR_NAME."""
    url = _compose({}, tmp_path)
    assert url.endswith("/tenant_inst"), url
