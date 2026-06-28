"""Proof for T-loguru-ownership-to-base.

The stdlib→loguru bridge is base substrate: owned by ``diagnostic_base``,
installed at device boot (NOT at import), and no longer owned/gated by Igor.
These checks fail on a hollow build (loguru still in Igor, install moved back
to import time, or the bridge severed from device boot).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_no_direct_loguru_import_under_igor() -> None:
    """Igor must not import loguru directly — ownership lives in the base."""
    offenders = []
    for py in (REPO / "devices" / "igor").rglob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("from loguru") or s.startswith("import loguru"):
                offenders.append(f"{py}: {s}")
    assert not offenders, f"direct loguru imports under devices/igor: {offenders}"


def _probe(snippet: str) -> list[str]:
    """Run a snippet in a clean interpreter and return its stdout tokens."""
    out = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_importing_base_does_not_install_intercept() -> None:
    """Merely importing the base must NOT clobber root logging handlers."""
    tokens = _probe(
        "import logging; import diagnostic_base.base as b; "
        "from diagnostic_base.logging_bridge import InterceptHandler; "
        "root=logging.getLogger(); "
        "print(any(isinstance(h, InterceptHandler) for h in root.handlers)); "
        "print(b._intercept_installed)"
    )
    assert tokens == ["False", "False"], tokens


def test_device_boot_installs_intercept() -> None:
    """Instantiating any DiagnosticBase device installs the bridge in-process."""
    tokens = _probe(
        "import logging; import diagnostic_base.base as b; "
        "from diagnostic_base.logging_bridge import InterceptHandler; "
        "b.DiagnosticBase(device_id='probe'); "
        "root=logging.getLogger(); "
        "print(any(isinstance(h, InterceptHandler) for h in root.handlers)); "
        "print(b._intercept_installed)"
    )
    assert tokens == ["True", "True"], tokens


def test_rack_and_devices_are_diagnosticbase() -> None:
    """Topology invariant: the rack runtime (Skeleton) and every device are
    DiagnosticBase, so each such process installs common logging on its own boot.

    Shims live process-wise in the rack; that process is covered because the
    Skeleton hosting them is itself a device and boots before it starts shims.
    The ground loop is deliberately NOT a device (lightweight resilience anchor),
    so it carries no loguru dependency.
    """
    from unseen_university.diagnostic_base.base import DiagnosticBase
    from unseen_university.device import BaseDevice
    from unseen_university.devices.skeleton.skeleton import Skeleton

    assert issubclass(BaseDevice, DiagnosticBase)
    assert issubclass(Skeleton, DiagnosticBase)
