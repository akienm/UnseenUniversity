"""Isolation strategies for the tester rackmount. (STUB — see T-tester-rackmount.)"""
from __future__ import annotations
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_FORBIDDEN = ("10.0.0.100", 11434)
PG_SOCKET_DIR = "/var/run/postgresql"


@dataclass(frozen=True)
class Seal:
    confirmed: bool
    detail: str


class Isolation(ABC):
    name: str = "abstract"
    seals_network: bool = False

    @abstractmethod
    def wrap(self, argv: list[str], cwd: str) -> list[str]: ...

    def available(self) -> tuple[bool, str]:
        return True, "stub"

    def check_seal(self, cwd: str, forbidden=DEFAULT_FORBIDDEN) -> Seal:
        # STUB: the seal is ASSERTED, not measured. This is the hollow build — it is what
        # ContainerShim's 27 mocked tests amount to, and the proof exists to fail it.
        return Seal(True, "assumed sealed")


class NoIsolation(Isolation):
    name = "none"
    seals_network = False

    def wrap(self, argv: list[str], cwd: str) -> list[str]:
        return list(argv)


class NetnsIsolation(Isolation):
    name = "netns"
    seals_network = True

    def wrap(self, argv: list[str], cwd: str) -> list[str]:
        return list(argv)   # STUB: no sandbox at all — runs on the host


def get_isolation(name: str) -> Isolation:
    return NetnsIsolation() if name == "netns" else NoIsolation()
