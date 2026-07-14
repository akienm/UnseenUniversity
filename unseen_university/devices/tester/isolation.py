"""Isolation strategies for the tester rackmount.

THE POINT, IN ONE SENTENCE: a test process must have NO ROUTE to a constrained shared
resource, so that "don't hammer Hex" stops being a policy people remember and becomes a
property of the kernel.

WHY A POLICY WAS NOT ENOUGH (2026-07-13, measured). Nine `pytest tests/` runs, shelled on the
host, each drove live 24B inference at Hex's SINGLE inference slot (`-np 1`). They never
terminated. They saturated it. The resulting queue produced two confidently-wrong diagnoses and
two false artifacts in the memory store before the load was traced back to us. A rule ("run tests
in series", "don't touch Hex") binds only the consumers who read it. `--unshare-net` binds every
consumer, including the ones nobody remembered to tell.

THE SEAL IS MEASURED, NEVER ASSUMED — this is the load-bearing design decision.
An isolation strategy that merely *claims* to be sealed is one more green light that means
nothing. `ContainerShim` has 27 passing tests and has never run a real container: they mock
`subprocess`, so they prove it assembles the right argv, not that anything is isolated. That is a
green suite indistinguishable from a working capability — the exact defect the tester exists to
kill. So every isolated run PROBES ITS OWN SEAL FROM INSIDE, and a run whose seal cannot be
confirmed is INDETERMINATE. It is never GREEN.

Strategies:
  NetnsIsolation  — bubblewrap `--unshare-net`. Rootless, daemonless, sub-second. No TCP stack at
                    all, so Hex is `[Errno 101] Network is unreachable`. Postgres still reachable,
                    because a Unix socket is not the network. This is the hot path.
  NoIsolation     — a plain host subprocess. What aider's runner does today. Kept ONLY so the
                    tester can be asked for it EXPLICITLY and can then refuse to call the result
                    trustworthy. It is never the default and it never yields a sealed verdict.

T-tester-rackmount.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The resource whose scarcity started all this. The seal probe asks: can I reach it? The answer
# must be NO, or the sandbox is not a sandbox.
DEFAULT_FORBIDDEN = ("10.0.0.100", 11434)

# Postgres speaks over a Unix socket, which survives --unshare-net. That is the whole trick: the
# rack stays reachable, the network does not.
PG_SOCKET_DIR = "/var/run/postgresql"

_SEAL_PROBE = (
    "import socket,sys\n"
    "try:\n"
    "    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3).close()\n"
    "    print('REACHABLE')\n"
    "except OSError:\n"
    "    print('SEALED')\n"
)


@dataclass(frozen=True)
class Seal:
    """Did this sandbox actually deny the forbidden resource? Measured, from inside."""

    confirmed: bool
    detail: str


class Isolation(ABC):
    """A way to run a command such that it cannot reach what it must not reach."""

    name: str = "abstract"
    seals_network: bool = False

    @abstractmethod
    def wrap(self, argv: list[str], cwd: str) -> list[str]:
        """Return argv wrapped in this isolation."""

    def available(self) -> tuple[bool, str]:
        return True, "always available"

    def check_seal(self, cwd: str, forbidden: tuple[str, int] = DEFAULT_FORBIDDEN) -> Seal:
        """Run a probe INSIDE the sandbox and ask it whether it can reach the forbidden host.

        This is not ceremony. An isolation that is misconfigured, silently downgraded, or running
        on a kernel that refuses the namespace looks EXACTLY like one that works — until a test
        opens a socket. The only way to know is to try, from inside, every time.
        """
        host, port = forbidden
        argv = self.wrap(["python3", "-c", _SEAL_PROBE, host, str(port)], cwd=cwd)
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=30, cwd=cwd)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Seal(False, f"seal probe could not run: {type(exc).__name__}: {exc}")

        out = (r.stdout or "").strip()
        if "SEALED" in out:
            return Seal(True, f"{host}:{port} unreachable from inside ({self.name})")
        if "REACHABLE" in out:
            return Seal(False, f"{host}:{port} IS REACHABLE from inside {self.name} — NOT SEALED")
        return Seal(False, f"seal probe gave no verdict: rc={r.returncode} out={out!r} err={(r.stderr or '')[:200]!r}")


class NoIsolation(Isolation):
    """A bare host subprocess. Exactly what aider's runner does today, and what CC did nine times.

    Kept so that "unisolated" is a thing you must ASK for by name, and so the tester can hand back
    a verdict that says, in the record, that nothing was sealed. An unnamed default is how the
    host-shelling kept happening without anyone deciding to do it.
    """

    name = "none"
    seals_network = False

    def wrap(self, argv: list[str], cwd: str) -> list[str]:
        return list(argv)

    def check_seal(self, cwd: str, forbidden: tuple[str, int] = DEFAULT_FORBIDDEN) -> Seal:
        return Seal(False, "no isolation requested — nothing is sealed, and the verdict says so")


class NetnsIsolation(Isolation):
    """bubblewrap `--unshare-net`: a fresh network namespace with no route anywhere.

    Rootless (unprivileged user namespaces), daemonless, and sub-second — a full pytest run starts
    in ~0.1s, versus a Docker image build. `--dev-bind / /` keeps the filesystem intact (including
    the Postgres Unix socket, which is not network and therefore survives), while the network
    namespace has no interfaces, no routes, and no way to reach 10.0.0.100.

    Requires `kernel.apparmor_restrict_unprivileged_userns=0` on Ubuntu 24.04+, which ships it as
    1. `available()` reports the real reason when it is off — a sandbox that cannot be built must
    say so loudly, not degrade quietly to running on the host. Degrading quietly is how you get
    nine orphans.
    """

    name = "netns"
    seals_network = True

    def available(self) -> tuple[bool, str]:
        if not shutil.which("bwrap"):
            return False, "bubblewrap (bwrap) is not installed"
        try:
            with open("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") as fh:
                if fh.read().strip() == "1":
                    return False, (
                        "kernel.apparmor_restrict_unprivileged_userns=1 — unprivileged user "
                        "namespaces are blocked, so bwrap cannot create a network namespace. "
                        "Fix: sysctl -w kernel.apparmor_restrict_unprivileged_userns=0"
                    )
        except FileNotFoundError:
            pass  # not an Ubuntu-24.04-style kernel; the probe below is the real check anyway
        return True, "bwrap present, user namespaces permitted"

    def wrap(self, argv: list[str], cwd: str) -> list[str]:
        # `--dev-bind / /` already binds the WHOLE filesystem, which includes the Postgres Unix
        # socket. An extra `--bind /var/run/postgresql` is not merely redundant, it FAILS: /var/run
        # is a symlink to /run, so bwrap tries to mkdir a mountpoint that cannot exist. The socket
        # needs no special handling — it is a file, and files are not the network. That asymmetry
        # is the whole design, and it costs exactly zero flags.
        return ["bwrap", "--dev-bind", "/", "/", "--unshare-net", "--chdir", cwd] + list(argv)


def get_isolation(name: str) -> Isolation:
    if name == "netns":
        return NetnsIsolation()
    if name == "none":
        return NoIsolation()
    raise ValueError(f"unknown isolation {name!r} — expected 'netns' or 'none'")
