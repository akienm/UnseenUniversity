"""TesterDevice — the first dev-pipeline station. An INDEPENDENT grader.

SEPARATION OF POWERS: THE BUILDER NEVER GRADES ITS OWN WORK.
Today aider's runner shells `pytest` in its own clone, on the host, and reads its own exit code
(`devices/aider/runner.py:_run_tests`). The thing that produced the diff decides whether the diff
is good. On 2026-07-13 CC did the same by hand, nine times, and the runs saturated Hex's single
inference slot — then CC diagnosed the saturation it had caused as a broken server, twice. A
grader that is downstream of the builder is not a check; it is the builder's opinion with a
process boundary around it.

So this device takes a repo and a set of test paths, runs them somewhere the builder cannot reach,
and returns a verdict the builder had no hand in.

THREE VERDICTS, AND THE THIRD IS THE IMPORTANT ONE.
    GREEN         — the tests passed, in a sandbox whose seal was CONFIRMED FROM INSIDE.
    RED           — the tests failed. A hollow build earns this and cannot argue.
    INDETERMINATE — we could not establish that the run was trustworthy.

**INDETERMINATE IS NOT GREEN.** It is the whole reason this class exists. An unsealed sandbox, a
missing bwrap, a kernel that refuses the namespace, a probe that gave no answer — every one of
those looks *exactly* like a healthy run right up until it doesn't, and collapsing them into
"passed" is the same non-injective map that let a crash wear CP1's clothes, let a saturated ollama
report healthy, and let 27 mocked tests stand in for a container that has never run. Undetermined
must never read as OK.

T-tester-rackmount. D-dev-pipeline-stations-2026-07-07.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.tester.isolation import (
    DEFAULT_FORBIDDEN,
    PG_SOCKET_DIR,
    Isolation,
    get_isolation,
)

log = logging.getLogger(__name__)

_START_TIME = time.time()
DEFAULT_TIMEOUT = 1800.0

GREEN = "GREEN"
RED = "RED"
INDETERMINATE = "INDETERMINATE"

_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors|skipped|deselected)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tcp_url_to_socket(url: str) -> str:
    """Rewrite a TCP Postgres URL to speak over the Unix socket instead.

    Inside `--unshare-net` there is no TCP stack, so `host=localhost` cannot resolve — but the
    Postgres Unix socket is a FILE, and files survive a network namespace. This is the seam that
    lets a fully network-sealed test still reach the rack's database: the socket is the only
    channel in or out, which is exactly ContainerShim's design, arrived at from the other end.
    """
    if not url or "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, hostpart = rest.rpartition("@")
    _, _, dbname = hostpart.partition("/")
    dbname = dbname.split("?", 1)[0]
    if not dbname:
        return url
    return f"{scheme}://{creds}@/{dbname}?host={PG_SOCKET_DIR}"


class TesterDevice(BaseDevice):
    """Runs a builder's tests in isolation and returns a verdict the builder cannot influence."""

    DEVICE_ID = "tester"
    __test__ = False  # the name starts with "Test"; pytest must not try to COLLECT the grader

    def __init__(self, isolation: str = "netns") -> None:
        super().__init__()
        self._isolation_name = isolation
        self._errors: list[str] = []
        self._runs = 0

    # ── the job ───────────────────────────────────────────────────────────────

    def run_tests(
        self,
        repo: str,
        test_paths: list[str] | None = None,
        isolation: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        python: str | None = None,
        forbidden: tuple[str, int] = DEFAULT_FORBIDDEN,
    ) -> dict:
        """Grade a build. Returns a verdict dict; NEVER raises for a failing build.

        A failing build is DATA (CP2), so it comes back as RED. Only a broken *grader* — one that
        cannot establish whether its own sandbox held — comes back INDETERMINATE.
        """
        iso: Isolation = get_isolation(isolation or self._isolation_name)
        started = time.time()
        self._runs += 1

        verdict = {
            "verdict": INDETERMINATE,
            "passed": None,
            "isolation": iso.name,
            "seal_confirmed": False,
            "seal_detail": "",
            "counts": {},
            "returncode": None,
            "tail": "",
            "duration_s": 0.0,
            "checked_at": _now(),
        }

        ok, why = iso.available()
        if not ok:
            # A sandbox that cannot be built must SAY SO, never quietly degrade to the host.
            # Quiet degradation is precisely how the host-shelling kept happening.
            verdict["seal_detail"] = f"isolation {iso.name!r} unavailable: {why}"
            log.error("TesterDevice: %s", verdict["seal_detail"])
            self._errors.append(verdict["seal_detail"])
            verdict["duration_s"] = time.time() - started
            return verdict

        # MEASURE THE SEAL, FROM INSIDE, EVERY RUN. Never trust the flag.
        seal = iso.check_seal(cwd=repo, forbidden=forbidden)
        verdict["seal_confirmed"] = seal.confirmed
        verdict["seal_detail"] = seal.detail
        log.info(
            "TesterDevice: isolation=%s seal_confirmed=%s (%s)",
            iso.name, seal.confirmed, seal.detail,
        )

        argv = iso.wrap(
            [python or "python3", "-m", "pytest", "-q", "--tb=line", *(test_paths or [])],
            cwd=repo,
        )
        env = dict(os.environ)
        db = env.get("UU_HOME_DB_URL", "")
        if iso.seals_network and db:
            # No TCP inside the namespace — speak to Postgres over its socket instead.
            env["UU_HOME_DB_URL"] = _tcp_url_to_socket(db)

        try:
            proc = subprocess.run(
                argv, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            # A test run that will not end is the failure that started all this. It is not a RED
            # (we learned nothing about the build) and it is emphatically not a GREEN.
            verdict["seal_detail"] += f" | run exceeded {timeout}s and was killed"
            verdict["duration_s"] = time.time() - started
            log.error("TesterDevice: run exceeded %.0fs — killed", timeout)
            return verdict
        except OSError as exc:
            verdict["seal_detail"] += f" | could not launch: {exc}"
            verdict["duration_s"] = time.time() - started
            return verdict

        out = (proc.stdout or "") + (proc.stderr or "")
        verdict["returncode"] = proc.returncode
        verdict["counts"] = {k if k != "errors" else "error": int(n) for n, k in _COUNTS.findall(out)}
        verdict["tail"] = "\n".join(out.strip().splitlines()[-15:])
        verdict["duration_s"] = time.time() - started

        if not seal.confirmed:
            # The tests may well have passed. We cannot say they passed *honestly*, because we
            # cannot say what they were allowed to touch. That is INDETERMINATE, not GREEN.
            log.warning("TesterDevice: refusing to certify an unsealed run — INDETERMINATE")
            return verdict

        passed = proc.returncode == 0
        verdict["passed"] = passed
        verdict["verdict"] = GREEN if passed else RED
        log.info(
            "TesterDevice: verdict=%s rc=%s counts=%s in %.1fs",
            verdict["verdict"], proc.returncode, verdict["counts"], verdict["duration_s"],
        )
        return verdict

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Tester",
            "version": "0.1.0",
            "purpose": "independent grader — runs a builder's tests in isolation, returns a verdict",
        }

    def requirements(self) -> dict:
        return {
            "deps": [],
            "system": [
                "bubblewrap (bwrap) for netns isolation",
                "kernel.apparmor_restrict_unprivileged_userns=0",
            ],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["test_verdict"],
            "mcp_tools": ["run_tests"],
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        ok, why = get_isolation(self._isolation_name).available()
        if not ok:
            return {"status": "degraded", "detail": why, "checked_at": _now()}
        if self._errors:
            return {"status": "degraded", "detail": self._errors[-1], "checked_at": _now()}
        return {
            "status": "healthy",
            "detail": f"isolation={self._isolation_name} runs={self._runs}",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._errors)

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.uname().nodename,
            "pid": os.getpid(),
            "launch_command": "python -m unseen_university.devices.tester",
        }

    def restart(self) -> None:
        self._errors.clear()

    def block(self, reason: str) -> None:
        self._errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._errors.clear()
