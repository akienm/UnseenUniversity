"""NetworkPolicy — the tester IS the local network.

AKIEN, 2026-07-14: *"the testing container should be itself the router for the local network. so we
can literally turn on and off individual ports as part of the testing protocol. or repoint them to
fixtures, or whatever we wish to do."*

HOW IT IS POSSIBLE, WITH NO NEW DEPENDENCIES. Inside `bwrap --unshare-net --cap-add CAP_NET_ADMIN
--uid 0` we are root *in our own user namespace*, which means we may configure our own network
namespace. So we do the one thing that makes everything else fall out:

    ip addr add 10.0.0.100/32 dev lo      # we CLAIM Hex's address
    bind(("10.0.0.100", 11434))           # and we ARE Hex, in here

The code under test never changes. `INFERENCE_ENDPOINT` still says Hex. The device still dials Hex.
It reaches whatever we have decided Hex is today.

THREE ACTIONS, AND THE DEFAULT IS DELIBERATE:

  FIXTURE — we claim the address and our handler answers at it. A dependency becomes something we
            SERVE, so a failing model is a thing we can ASK FOR rather than wait to be ambushed by.
  REFUSE  — an nftables rule in our own netns: `counter reject with tcp reset`. The caller gets a
            genuine, immediate ECONNREFUSED, **and the kernel counts the attempt anyway.** Default.
  DENY    — we never claim the address: ENETUNREACH, indistinguishable from a dead box. Honest, and
            invisible. Available per-rule for tests that must face a genuinely absent host.

**REFUSE HAD TO BE A KERNEL RULE, AND THE REASON IS THE POINT.** You cannot both refuse a TCP
connection and observe it in userspace: a true ECONNREFUSED requires that NOTHING be listening, and
if nothing listens there is nothing to record. The first cut of this module listened, accepted and
closed — so the caller's `connect()` SUCCEEDED, which is not a refusal at all, merely the shape of
one. nftables settles it: `reject` is real, `counter` is exact.

**AND THAT COUNTER IS THE WHOLE LESSON OF 2026-07-13 MADE STRUCTURAL.** Verifying that the suite no
longer touched Hex, I sampled `ss` once per SECOND for connections that live 2-4 MILLISECONDS, got a
clean zero, and read the silence as proof. **An instrument too coarse to see the event is not
evidence of absence.** A kernel counter does not sample the event — it IS the event. So "which tests
reach for the network, and for what" stops being something we infer and becomes something we know.

And it settles admission control: a policy binds only the consumers who read it. **A socket that
does not exist binds all of them.**

T-tester-owns-the-network.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer

from unseen_university.devices.tester.fixtures import FIXTURES

log = logging.getLogger(__name__)

_NFT_TABLE = "uu_tester"

FIXTURE = "fixture"
REFUSE = "refuse"
DENY = "deny"


@dataclass(frozen=True)
class Rule:
    """What does (host, port) mean inside this sandbox?"""

    host: str
    port: int
    action: str = REFUSE
    fixture: str = ""      # a key of FIXTURES, when action == FIXTURE

    def __post_init__(self):
        if self.action not in (FIXTURE, REFUSE, DENY):
            raise ValueError(f"unknown action {self.action!r}")
        if self.action == FIXTURE and self.fixture not in FIXTURES:
            raise ValueError(
                f"unknown fixture {self.fixture!r} — have {sorted(FIXTURES)}"
            )

    @property
    def claims_address(self) -> bool:
        """DENY is the only action that leaves the address unrouted."""
        return self.action != DENY


@dataclass
class NetworkPolicy:
    """The network a test run is allowed to see."""

    rules: list[Rule] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps([
            {"host": r.host, "port": r.port, "action": r.action, "fixture": r.fixture}
            for r in self.rules
        ])

    @staticmethod
    def from_json(raw: str) -> "NetworkPolicy":
        return NetworkPolicy([Rule(**d) for d in json.loads(raw)])

    @staticmethod
    def hex_serves(fixture: str) -> "NetworkPolicy":
        """Hex's address, answering with the named fixture. The common case."""
        return NetworkPolicy([Rule("10.0.0.100", 11434, FIXTURE, fixture)])


# ── the router. Runs INSIDE the sandbox, where we own the netns. ──────────────


class Router:
    """Claims the addresses, serves the fixtures, records every attempt."""

    def __init__(self, policy: NetworkPolicy) -> None:
        self._policy = policy
        self._servers: list[ThreadingHTTPServer] = []
        self._counted: list[Rule] = []
        self._nft_ready = False
        self.attempts: list[dict] = []
        self._lock = threading.Lock()

    # -- netns setup ----------------------------------------------------------

    @staticmethod
    def _ip(*args: str) -> tuple[int, str]:
        r = subprocess.run(["ip", *args], capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    def bring_up(self) -> None:
        """Configure our own netns. Requires CAP_NET_ADMIN *in the user namespace*."""
        rc, out = self._ip("link", "set", "lo", "up")
        if rc:
            raise RuntimeError(f"cannot bring up lo — no CAP_NET_ADMIN in this netns? {out}")

        for rule in self._policy.rules:
            if not rule.claims_address:
                continue   # DENY: leave it unrouted, so it is genuinely unreachable
            if rule.host not in ("127.0.0.1", "localhost"):
                rc, out = self._ip("addr", "add", f"{rule.host}/32", "dev", "lo")
                if rc and "File exists" not in out:
                    raise RuntimeError(f"cannot claim {rule.host}: {out}")

            if rule.action == FIXTURE:
                self._serve(rule)
            else:  # REFUSE — an nft reject+counter rule: a true ECONNREFUSED that is still SEEN
                self._refuse(rule)

    def _serve(self, rule: Rule) -> None:
        handler = FIXTURES[rule.fixture]
        router = self

        class _Recording(handler):  # type: ignore[misc,valid-type]
            def handle_one_request(self):
                router._record(rule.host, rule.port, "fixture:" + rule.fixture, self.path if hasattr(self, "path") else "")
                return super().handle_one_request()

        srv = ThreadingHTTPServer((rule.host, rule.port), _Recording)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self._servers.append(srv)
        log.info("Router: %s:%s -> FIXTURE %s", rule.host, rule.port, rule.fixture)

    def _refuse(self, rule: Rule) -> None:
        """A TRUE refusal that is nonetheless COUNTED. nftables, in our own netns.

        THE CONSTRAINT THAT FORCED THIS, and it is real: **you cannot both refuse a TCP connection
        and observe it in userspace.** A true `ECONNREFUSED` requires that NOTHING be listening — and
        if nothing listens, there is nothing to record. The first cut of this method listened,
        accepted, and closed; the client's `connect()` therefore SUCCEEDED, which is not a refusal
        at all. A sandbox that says "refused" while handing out live sockets would be one more thing
        that reports the shape of the truth and not the truth.

        The kernel settles it. We are root in our own user namespace, so we own the netns, so we own
        its firewall:

            nft add rule ... ip daddr <host> tcp dport <port> counter reject with tcp reset

        The caller gets a genuine, immediate `ECONNREFUSED` — and the counter increments anyway.
        **A kernel counter cannot miss a 3-millisecond connection**, which is precisely what my
        once-per-second `ss` sampling did on 2026-07-13 before I read the silence as proof.
        """
        self._nft_init()
        rc, out = self._nft(
            "add", "rule", "inet", _NFT_TABLE, "input",
            "ip", "daddr", rule.host, "tcp", "dport", str(rule.port),
            "counter", "reject", "with", "tcp", "reset",
        )
        if rc:
            raise RuntimeError(f"cannot arm the refuse rule for {rule.host}:{rule.port}: {out}")
        self._counted.append(rule)
        log.info("Router: %s:%s -> REFUSE (true ECONNREFUSED, kernel-counted)", rule.host, rule.port)

    # -- nftables ------------------------------------------------------------

    @staticmethod
    def _nft(*args: str) -> tuple[int, str]:
        r = subprocess.run(["nft", *args], capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    def _nft_init(self) -> None:
        if self._nft_ready:
            return
        rc, out = self._nft("add", "table", "inet", _NFT_TABLE)
        if rc:
            raise RuntimeError(f"cannot create nft table (is nftables installed?): {out}")
        rc, out = self._nft(
            "add", "chain", "inet", _NFT_TABLE, "input",
            "{ type filter hook input priority 0; }",
        )
        if rc:
            raise RuntimeError(f"cannot create nft chain: {out}")
        self._nft_ready = True

    def _harvest_counters(self) -> None:
        """Read the kernel's tally of every refused attempt. It cannot have missed one."""
        rc, out = self._nft("list", "table", "inet", _NFT_TABLE)
        if rc:
            return
        for rule in self._counted:
            m = re.search(
                rf"ip daddr {re.escape(rule.host)} tcp dport {rule.port} counter packets (\d+)",
                out,
            )
            n = int(m.group(1)) if m else 0
            for _ in range(n):
                self._record(rule.host, rule.port, "refused", "")

    def _record(self, host: str, port: int, action: str, path: str) -> None:
        with self._lock:
            self.attempts.append({"host": host, "port": port, "action": action, "path": path})

    def shutdown(self) -> None:
        self._harvest_counters()      # ask the kernel what it saw, before we tear anything down
        for srv in self._servers:
            srv.shutdown()
