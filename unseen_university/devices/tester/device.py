"""TesterDevice — independent grader. (STUB — see T-tester-rackmount.)"""
from __future__ import annotations
import os, re, subprocess, time
from datetime import datetime, timezone
from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.tester.isolation import DEFAULT_FORBIDDEN, PG_SOCKET_DIR, get_isolation

_START_TIME = time.time()
DEFAULT_TIMEOUT = 1800.0
GREEN, RED, INDETERMINATE = "GREEN", "RED", "INDETERMINATE"
_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors|skipped|deselected)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tcp_url_to_socket(url: str) -> str:
    return url


class TesterDevice(BaseDevice):
    DEVICE_ID = "tester"
    __test__ = False

    def __init__(self, isolation: str = "netns") -> None:
        super().__init__()
        self._isolation_name = isolation
        self._errors: list[str] = []
        self._runs = 0

    def run_tests(self, repo, test_paths=None, isolation=None, timeout=DEFAULT_TIMEOUT,
                  python=None, forbidden=DEFAULT_FORBIDDEN) -> dict:
        iso = get_isolation(isolation or self._isolation_name)
        self._runs += 1
        seal = iso.check_seal(cwd=repo, forbidden=forbidden)   # STUB: always "sealed"
        argv = iso.wrap([python or "python3", "-m", "pytest", "-q", "--tb=line", *(test_paths or [])], cwd=repo)
        proc = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        counts = {k if k != "errors" else "error": int(n) for n, k in _COUNTS.findall(out)}
        passed = proc.returncode == 0
        return {
            "verdict": GREEN if passed else RED, "passed": passed,
            "isolation": iso.name, "seal_confirmed": seal.confirmed, "seal_detail": seal.detail,
            "counts": counts, "returncode": proc.returncode,
            "tail": "\n".join(out.strip().splitlines()[-15:]),
            "duration_s": 0.0, "checked_at": _now(),
        }

    def who_am_i(self): return {"device_id": self.DEVICE_ID, "name": "Tester", "version": "0.1.0", "purpose": "independent grader"}
    def requirements(self): return {"deps": [], "system": []}
    def capabilities(self): return {"can_send": False, "can_receive": True, "emitted_keywords": ["test_verdict"], "mcp_tools": ["run_tests"]}
    def comms(self): return {"address": f"comms://{self.DEVICE_ID}/inbox", "mode": "read_write", "supports_push": False, "supports_pull": True, "supports_nudge": False}
    def interface_version(self): return INTERFACE_VERSION
    def health(self): return {"status": "healthy", "detail": "stub", "checked_at": _now()}
    def uptime(self): return time.time() - _START_TIME
    def startup_errors(self): return list(self._errors)
    def logs(self): return {"paths": {}}
    def update_info(self): return {"current_version": "0.1.0", "update_available": False}
    def where_and_how(self): return {"host": os.uname().nodename, "pid": os.getpid(), "launch_command": "python -m unseen_university.devices.tester"}
    def restart(self): self._errors.clear()
    def block(self, reason): self._errors.append(f"blocked: {reason}")
    def halt(self): pass
    def recovery(self): self._errors.clear()
