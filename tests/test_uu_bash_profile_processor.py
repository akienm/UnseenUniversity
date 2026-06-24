"""Proof for the secret-free config-identity processor (T-uu-config-profile-layer).

The processor composes UU_HOME_DB_URL + hostname-derived names from bootstrap vars,
contains no credential, and must never break or hang a shell (CP6 recovery contract).

These run in a CLEAN env (only the bootstrap vars exist) so the compose path is
actually exercised — a `bash -l` that inherited UU_HOME_DB_URL would pass on hollow
output without ever composing. That clean-env discrimination is the whole point.

Bootstrap values here are SYNTHETIC: this decision is scrubbing the real password from
the repo, so the test must not re-introduce it. Behavior-preservation against the real
current URL is checked by the env-driven round-trip test, which embeds nothing.
"""
import os
import re
import subprocess
import tempfile
import time
import urllib.parse as urlparse
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PROC = REPO / "bin" / "uu_bash_profile_processor.sh"

# The processor now emits $HOME/.unseen_university/uu.env when a URL composes. Point the
# clean-env HOME at a throwaway dir so that side-effect of the compose-path tests never
# touches /tmp/.unseen_university (cross-test pollution / parallel-run race).
_TMP_HOME = tempfile.mkdtemp(prefix="uu_proc_test_home_")

BOOTSTRAP = {
    "UU_DB_USER": "testuser",
    "UU_DB_PASSWORD": "testpass",
    "UU_DB_IP": "127.0.0.1",
    "IGOR_NAME": "Test-instance-0001",
}
EXPECTED_URL = "postgresql://testuser:testpass@127.0.0.1/Test-instance-0001"

_BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
HOSTNAME = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()


def _clean_env(extra):
    # env -i equivalent: only PATH/HOME (so `hostname` resolves) plus the given vars.
    env = {"PATH": _BASE_PATH, "HOME": _TMP_HOME}
    env.update(extra)
    return env


def _run(script, extra):
    return subprocess.run(
        ["bash", "-c", script], env=_clean_env(extra),
        capture_output=True, text=True, timeout=10,
    )


def test_composes_db_url_from_bootstrap_in_clean_env():
    r = _run(f'source "{PROC}"; printf "%s" "$UU_HOME_DB_URL"', BOOTSTRAP)
    assert r.returncode == 0, r.stderr
    assert r.stdout == EXPECTED_URL


def test_igor_swarm_name_is_hostname():
    r = _run(f'source "{PROC}"; printf "%s" "$IGOR_SWARM_NAME"', BOOTSTRAP)
    assert r.returncode == 0, r.stderr
    assert r.stdout == HOSTNAME


def test_instance_id_tracks_igor_name():
    r = _run(f'source "{PROC}"; printf "%s" "$IGOR_INSTANCE_ID"', BOOTSTRAP)
    assert r.stdout == "Test-instance-0001"


def test_home_db_ip_overrides_db_ip():
    env = dict(BOOTSTRAP, UU_HOME_DB_IP="10.0.0.5")
    r = _run(f'source "{PROC}"; printf "%s" "$UU_HOME_DB_URL"', env)
    assert r.stdout == "postgresql://testuser:testpass@10.0.0.5/Test-instance-0001"


def test_processor_contains_no_credential_literal():
    text = PROC.read_text()
    # Build the needle from fragments so this test file itself stays scrub-clean.
    assert ("choose_a" + "_password") not in text
    # No literal colon-password-at sequence in the source.
    assert re.search(r":[^@/]+@", text) is None


def test_fail_soft_when_bootstrap_absent():
    # No bootstrap vars => composes nothing, still rc 0, fast (recovery contract).
    t0 = time.monotonic()
    r = _run(f'source "{PROC}"; echo "rc=$?"', {})
    assert r.returncode == 0, r.stderr
    assert "rc=0" in r.stdout
    assert (time.monotonic() - t0) < 2.0
    # And it must NOT have invented a URL out of nothing.
    r2 = _run(f'source "{PROC}"; printf "[%s]" "$UU_HOME_DB_URL"', {})
    assert r2.stdout == "[]"


def test_emits_sourceable_env_file_for_noninteractive(tmp_path):
    """Proof node (one intention): a process with NO login shell, sourcing ONLY the
    emitted uu.env, resolves UU_HOME_DB_URL. That is the whole point of the env file —
    systemd units / cron-heartbeat shells / Ground-Loop-woken devices like Nanny that
    can't source the bash profile still get the composed DB URL (T-uu-env-file-for-noninteractive)."""
    env_file = tmp_path / ".unseen_university" / "uu.env"
    r = _run(f'source "{PROC}"', dict(BOOTSTRAP, HOME=str(tmp_path)))
    assert r.returncode == 0, r.stderr
    assert env_file.exists(), "processor did not emit uu.env"
    # chmod 600 — it carries the DB password
    assert (env_file.stat().st_mode & 0o777) == 0o600, oct(env_file.stat().st_mode & 0o777)
    # discriminating check: a CLEAN process (no profile, no inherited env, no HOME) that
    # sources ONLY uu.env resolves the URL.
    r2 = subprocess.run(
        ["bash", "-c", f'set -a; . "{env_file}"; printf "%s" "$UU_HOME_DB_URL"'],
        env={"PATH": _BASE_PATH}, capture_output=True, text=True, timeout=10,
    )
    assert r2.stdout == EXPECTED_URL, f"{r2.stdout!r} != {EXPECTED_URL!r}"


def test_no_env_file_emitted_without_bootstrap(tmp_path):
    """No bootstrap => no composed URL => DON'T write the file (never clobber a good
    uu.env with blanks from a recovery shell). Recovery-safe (CP6)."""
    r = _run(f'source "{PROC}"', {"HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / ".unseen_university" / "uu.env").exists()


def test_round_trip_preserves_current_url():
    """Behavior preservation: feeding the CURRENT url's parts back through the
    processor must reproduce it byte-for-byte. Embeds nothing — reads the live env."""
    cur = os.environ.get("UU_HOME_DB_URL", "")
    if not cur:
        pytest.skip("UU_HOME_DB_URL not in env — nothing to round-trip against")
    p = urlparse.urlparse(cur)
    extra = {
        "UU_DB_USER": p.username or "",
        "UU_DB_PASSWORD": p.password or "",
        "UU_DB_IP": p.hostname or "",
        "IGOR_NAME": p.path.lstrip("/"),
    }
    r = _run(f'source "{PROC}"; printf "%s" "$UU_HOME_DB_URL"', extra)
    assert r.returncode == 0, r.stderr
    assert r.stdout == cur
