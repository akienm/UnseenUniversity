"""
cron_backend.py — OS cron abstraction for Nanny Ogg.

Manages OS-level cron jobs via an abstract interface. Linux implementation
reads/writes crontab via subprocess. Windows implementation is a stub.

Only Nanny Ogg calls this module — scheduling is rule-based, zero inference.

D-nanny-ogg-device-2026-06-09
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CronJob:
    """A single cron job entry."""

    job_id: str          # "1", "2", etc. — 1-based position among cron lines
    expr: str            # 5-field cron expression (e.g. "0 3 * * *")
    cmd: str             # shell command
    enabled: bool        # True unless the line is commented out
    raw_line: str        # original crontab line (for round-trip accuracy)


class CronBackend(ABC):
    """Abstract interface for OS cron management."""

    @abstractmethod
    def list_jobs(self) -> list[CronJob]:
        """Return all cron jobs (enabled + disabled)."""

    @abstractmethod
    def add_job(self, expr: str, cmd: str) -> CronJob:
        """Append a new cron job. Returns the created job."""

    @abstractmethod
    def disable_job(self, job_id: str) -> bool:
        """Comment out job_id. Returns True on success."""

    @abstractmethod
    def enable_job(self, job_id: str) -> bool:
        """Uncomment job_id. Returns True on success."""

    @abstractmethod
    def run_now(self, job_id: str) -> subprocess.CompletedProcess | None:
        """Execute job_id immediately. Returns CompletedProcess or None."""


class LinuxCronBackend(CronBackend):
    """Cron backend for Linux/macOS — reads and writes via `crontab` subprocess."""

    # Prefix used to mark lines disabled by Nanny
    _DISABLED_PREFIX = "#NANNY_DISABLED:"

    def _read_crontab(self) -> list[str]:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # No crontab set for this user — treat as empty
            if "no crontab" in result.stderr.lower():
                return []
            raise RuntimeError(f"crontab -l failed: {result.stderr.strip()}")
        return result.stdout.splitlines()

    def _write_crontab(self, lines: list[str]) -> None:
        content = "\n".join(lines) + ("\n" if lines else "")
        result = subprocess.run(
            ["crontab", "-"],
            input=content, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"crontab write failed: {result.stderr.strip()}")

    def _is_cron_line(self, line: str) -> bool:
        """True for enabled cron lines (not blank, not pure comment)."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return False
        # Must have at least 6 space-separated fields (5 schedule + 1 cmd)
        return len(stripped.split()) >= 6

    def _is_disabled_line(self, line: str) -> bool:
        return line.startswith(self._DISABLED_PREFIX)

    def _parse_disabled(self, line: str) -> str:
        """Return the original cron line hidden inside a disabled marker."""
        return line[len(self._DISABLED_PREFIX):]

    def list_jobs(self) -> list[CronJob]:
        lines = self._read_crontab()
        jobs: list[CronJob] = []
        job_num = 0
        for line in lines:
            if self._is_cron_line(line):
                job_num += 1
                parts = line.split(None, 5)
                expr = " ".join(parts[:5])
                cmd = parts[5] if len(parts) > 5 else ""
                jobs.append(CronJob(
                    job_id=str(job_num),
                    expr=expr,
                    cmd=cmd,
                    enabled=True,
                    raw_line=line,
                ))
            elif self._is_disabled_line(line):
                inner = self._parse_disabled(line)
                if self._is_cron_line(inner):
                    job_num += 1
                    parts = inner.split(None, 5)
                    expr = " ".join(parts[:5])
                    cmd = parts[5] if len(parts) > 5 else ""
                    jobs.append(CronJob(
                        job_id=str(job_num),
                        expr=expr,
                        cmd=cmd,
                        enabled=False,
                        raw_line=line,
                    ))
        return jobs

    def add_job(self, expr: str, cmd: str) -> CronJob:
        lines = self._read_crontab()
        new_line = f"{expr} {cmd}"
        lines.append(new_line)
        self._write_crontab(lines)
        # ID is position among cron lines after write
        jobs = self.list_jobs()
        # Find the job we just added by matching the raw line
        for job in reversed(jobs):
            if job.raw_line == new_line:
                return job
        # Fallback: return last job
        return jobs[-1]

    def disable_job(self, job_id: str) -> bool:
        lines = self._read_crontab()
        jobs = self.list_jobs()
        target = next((j for j in jobs if j.job_id == job_id), None)
        if target is None or not target.enabled:
            return False
        # Replace the matching line with a disabled marker
        new_lines = []
        replaced = False
        for line in lines:
            if not replaced and line == target.raw_line:
                new_lines.append(f"{self._DISABLED_PREFIX}{line}")
                replaced = True
            else:
                new_lines.append(line)
        if replaced:
            self._write_crontab(new_lines)
        return replaced

    def enable_job(self, job_id: str) -> bool:
        lines = self._read_crontab()
        jobs = self.list_jobs()
        target = next((j for j in jobs if j.job_id == job_id), None)
        if target is None or target.enabled:
            return False
        new_lines = []
        restored = False
        for line in lines:
            if not restored and line == target.raw_line:
                new_lines.append(self._parse_disabled(line))
                restored = True
            else:
                new_lines.append(line)
        if restored:
            self._write_crontab(new_lines)
        return restored

    def run_now(self, job_id: str) -> subprocess.CompletedProcess | None:
        jobs = self.list_jobs()
        target = next((j for j in jobs if j.job_id == job_id), None)
        if target is None:
            return None
        return subprocess.run(
            target.cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )


class WindowsCronBackend(CronBackend):
    """Stub cron backend for Windows — Task Scheduler integration not yet implemented."""

    def list_jobs(self) -> list[CronJob]:
        raise NotImplementedError(
            "Windows Task Scheduler backend not yet implemented. "
            "Use the Task Scheduler GUI or schtasks.exe directly."
        )

    def add_job(self, expr: str, cmd: str) -> CronJob:
        raise NotImplementedError("Windows cron backend not implemented")

    def disable_job(self, job_id: str) -> bool:
        raise NotImplementedError("Windows cron backend not implemented")

    def enable_job(self, job_id: str) -> bool:
        raise NotImplementedError("Windows cron backend not implemented")

    def run_now(self, job_id: str) -> subprocess.CompletedProcess | None:
        raise NotImplementedError("Windows cron backend not implemented")


def get_cron_backend() -> CronBackend:
    """Return the appropriate CronBackend for the current OS."""
    system = platform.system()
    if system in ("Linux", "Darwin"):
        return LinuxCronBackend()
    elif system == "Windows":
        return WindowsCronBackend()
    else:
        raise RuntimeError(f"Unsupported OS for cron backend: {system}")
