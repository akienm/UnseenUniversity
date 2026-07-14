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
import os
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
FORWARD = "forward"


@dataclass(frozen=True)
class Rule:
    """What does (host, port) mean inside this sandbox?"""

    host: str
    port: int
    action: str = REFUSE
    fixture: str = ""      # a key of FIXTURES, when action == FIXTURE
    via: str = ""          # host-side Unix socket path, when action == FORWARD

    def __post_init__(self):
        if self.action not in (FIXTURE, REFUSE, DENY, FORWARD):
            raise ValueError(f"unknown action {self.action!r}")
        if self.action == FIXTURE and self.fixture not in FIXTURES:
            raise ValueError(
                f"unknown fixture {self.fixture!r} — have {sorted(FIXTURES)}"
            )
        # `via` is left EMPTY on purpose here: the caller declares WHAT to forward, and the device
        # decides WHERE the host-side socket lives (it owns the temp dir and the forwarder). The
        # Router enforces that it was filled in — see _forward — because by then it is a real
        # requirement rather than a premature one.

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
            {"host": r.host, "port": r.port, "action": r.action, "fixture": r.fixture, "via": r.via}
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
        self._forwarders: list[socket.socket] = []
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
            elif rule.action == FORWARD:
                self._forward(rule)
            else:  # REFUSE — an nft reject+counter rule: a true ECONNREFUSED that is still SEEN
                self._refuse(rule)

    def _forward(self, rule: Rule) -> None:
        """Bind (host, port) IN HERE and pipe every byte over a Unix socket to the host side.

        THE ONLY CHANNEL IN OR OUT IS A FILE. This is exactly ContainerShim's model — "the container
        gets no TCP stack at all... the only channel is the bind-mounted Unix socket" — reached from
        the other end, and it is what lets a fully network-sealed sandbox still talk to Postgres.

        Why not simply repoint the DB at its Unix socket and be done? Because that silently changes
        the AUTHENTICATION METHOD. Postgres applies `peer` auth to `local` connections (matching the
        OS uid against the role name) and password auth to `host` connections. Rewriting the URL to
        the socket therefore made 716 tests fail with "Peer authentication failed" — a sandbox that
        broke the thing it was meant to observe, which is the one sin a grader may not commit.

        Forwarding keeps the connection a TCP one from Postgres's point of view, so the URL, the
        credentials, and the auth path are all EXACTLY what they are in production. The sandbox
        stays transparent, and we still own every byte that crosses the boundary.
        """
        if not rule.via:
            raise RuntimeError(
                f"FORWARD {rule.host}:{rule.port} has no `via` socket — the device must resolve it "
                f"before the sandbox starts, or there is no door to forward through"
            )
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((rule.host, rule.port))
        srv.listen(64)
        self._forwarders.append(srv)

        def _pump(a: socket.socket, b: socket.socket) -> None:
            try:
                while chunk := a.recv(65536):
                    b.sendall(chunk)
            except OSError:
                pass
            finally:
                for s in (a, b):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    s.close()

        def _accept() -> None:
            while True:
                try:
                    client, _ = srv.accept()
                except OSError:
                    return
                self._record(rule.host, rule.port, "forwarded", "")
                try:
                    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    up.connect(rule.via)
                except OSError as exc:
                    log.warning("Router: forward %s:%s failed: %s", rule.host, rule.port, exc)
                    client.close()
                    continue
                threading.Thread(target=_pump, args=(client, up), daemon=True).start()
                threading.Thread(target=_pump, args=(up, client), daemon=True).start()

        threading.Thread(target=_accept, daemon=True).start()
        log.info("Router: %s:%s -> FORWARD via %s", rule.host, rule.port, rule.via)

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
        for f in self._forwarders:
            f.close()


# ── the HOST side of the channel ─────────────────────────────────────────────


class HostForwarder:
    """Runs on the HOST: a Unix socket that pipes to a real TCP service.

    The sandbox has no TCP stack, so it cannot reach Postgres — but a Unix socket is a FILE, and
    files cross a network namespace freely. This is the only door, and we are standing in it: every
    byte the sandbox sends to the outside world passes through here, which is what makes the policy
    ENFORCEABLE rather than merely declared.
    """

    def __init__(self, sock_path: str, host: str, port: int) -> None:
        self._path = sock_path
        self._target = (host, port)
        self._srv: socket.socket | None = None
        self.connections = 0

    def start(self) -> None:
        if os.path.exists(self._path):
            os.unlink(self._path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self._path)
        srv.listen(64)
        os.chmod(self._path, 0o666)
        self._srv = srv
        threading.Thread(target=self._accept, daemon=True).start()
        log.info("HostForwarder: %s -> %s:%s", self._path, *self._target)

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._srv.accept()   # type: ignore[union-attr]
            except OSError:
                return
            self.connections += 1
            try:
                up = socket.create_connection(self._target, timeout=10)
            except OSError as exc:
                log.warning("HostForwarder: cannot reach %s:%s — %s", *self._target, exc)
                client.close()
                continue
            for a, b in ((client, up), (up, client)):
                threading.Thread(target=_pipe, args=(a, b), daemon=True).start()

    def stop(self) -> None:
        if self._srv:
            self._srv.close()
        try:
            os.unlink(self._path)
        except OSError:
            pass


def _pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while chunk := a.recv(65536):
            b.sendall(chunk)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
