"""
Job Manager — persistent state for long-running multi-step tasks.

Long-running jobs are a different class from queries:
  - They span multiple turns and may survive restarts.
  - Progress is checkpointed after each batch so work is never lost.
  - Failed units are logged and skipped; the job continues.
  - On completion, the associated GitHub work order can be closed.

Storage: ~/.TheIgors/igor_wild_0001/jobs/{job_id}.json
Loaded on boot — pending/running/paused jobs resume automatically.

Trigger (called from main._process()):
  complexity.score > 0.6 AND complexity.is_multi_unit == True
  → Igor confirms "this is a long job, creating job #N"
"""

from __future__ import annotations
import logging

import json
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..igor_base import IgorBase
from ..paths import paths

JOBS_DIR = paths().jobs

_STATUS_ACTIVE = frozenset({"pending", "running", "paused"})


@dataclass
class Job:
    job_id: str
    title: str
    status: str  # pending | running | paused | completed | cancelled | failed
    created_at: str
    updated_at: str
    total_units: int = 0
    completed_units: int = 0
    failed_units: int = 0
    checkpoint: str = ""  # identifier of last successfully processed item
    result_path: str = ""
    github_issue: str = ""
    batch_size: int = 5
    notes: str = ""
    thread_id: str = (
        ""  # #159: originating attention nexus — for completion notification
    )

    def save(self) -> None:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        path = JOBS_DIR / f"{self.job_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, job_id: str) -> Optional["Job"]:
        path = JOBS_DIR / f"{job_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        except Exception:
            return None

    def progress_pct(self) -> float:
        if self.total_units <= 0:
            return 0.0
        return self.completed_units / self.total_units * 100

    def summary(self) -> str:
        pct = self.progress_pct()
        return (
            f"Job #{self.job_id} '{self.title}' [{self.status}] "
            f"{self.completed_units}/{self.total_units} ({pct:.0f}%) "
            f"failed={self.failed_units} "
            f"checkpoint='{self.checkpoint[:40]}'"
        )


class JobManager(IgorBase):
    """
    Manages the lifecycle of long-running jobs.
    Loaded at Igor boot; persists state to ~/.TheIgors/igor_wild_0001/jobs/.
    """

    def __init__(self) -> None:
        super().__init__()
        self._jobs: dict[str, Job] = {}
        self._load_active()

    # ── Boot ──────────────────────────────────────────────────────────────────

    # Pending jobs older than this are auto-cancelled at startup (never started = orphan)
    _STALE_PENDING_HOURS = 4

    def _load_active(self) -> None:
        """
        Load pending/running/paused jobs from disk on startup.
        Auto-cancels 'pending' jobs older than _STALE_PENDING_HOURS — these
        were created but never dispatched (pre-G4 orphans or interrupted sessions).
        'running' jobs are kept; they may be resumable.
        """
        if not JOBS_DIR.exists():
            return
        now = datetime.now()
        for path in sorted(JOBS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                j = Job(**data)
                if j.status == "pending":
                    # Auto-cancel stale pending jobs
                    age_hours = (
                        now - datetime.fromisoformat(j.updated_at)
                    ).total_seconds() / 3600
                    if age_hours > self._STALE_PENDING_HOURS:
                        j.status = "cancelled"
                        j.notes = (
                            j.notes + " | auto-cancelled: stale pending at startup"
                        ).strip()
                        j.save()
                        continue
                if j.status in _STATUS_ACTIVE:
                    self._jobs[j.job_id] = j
            except Exception as _bare_e:
                logging.getLogger(__name__).warning("bare except in wild_igor/igor/cognition/job_manager.py: %s", _bare_e)

    def active_count(self) -> int:
        return len(self._jobs)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(
        self,
        title: str,
        total_units: int = 0,
        batch_size: int = 5,
        github_issue: str = "",
        notes: str = "",
    ) -> Job:
        job_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        j = Job(
            job_id=job_id,
            title=title,
            status="pending",
            created_at=now,
            updated_at=now,
            total_units=total_units,
            batch_size=batch_size,
            github_issue=github_issue,
            notes=notes,
        )
        j.save()
        self._jobs[job_id] = j
        return j

    def get(self, job_id: str) -> Optional[Job]:
        if job_id in self._jobs:
            return self._jobs[job_id]
        return Job.load(job_id)

    def list_jobs(self, include_closed: bool = False) -> list[Job]:
        """Return jobs sorted newest-first. include_closed loads all .json files."""
        if include_closed:
            jobs = []
            if JOBS_DIR.exists():
                for path in sorted(JOBS_DIR.glob("*.json"), reverse=True):
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        jobs.append(Job(**data))
                    except Exception as _bare_e:
                        logging.getLogger(__name__).warning("bare except in wild_igor/igor/cognition/job_manager.py: %s", _bare_e)
            return jobs
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def _save(self, job: Job) -> None:
        job.updated_at = datetime.now().isoformat()
        job.save()
        if job.status in _STATUS_ACTIVE:
            self._jobs[job.job_id] = job
        else:
            self._jobs.pop(job.job_id, None)

    # ── State transitions ─────────────────────────────────────────────────────

    def start(self, job_id: str) -> Optional[Job]:
        j = self.get(job_id)
        if j and j.status == "pending":
            j.status = "running"
            self._save(j)
        return j

    def pause(self, job_id: str) -> Optional[Job]:
        j = self.get(job_id)
        if j and j.status == "running":
            j.status = "paused"
            self._save(j)
        return j

    def resume(self, job_id: str) -> Optional[Job]:
        j = self.get(job_id)
        if j and j.status == "paused":
            j.status = "running"
            self._save(j)
        return j

    def cancel(self, job_id: str) -> Optional[Job]:
        j = self.get(job_id)
        if j:
            j.status = "cancelled"
            self._save(j)
        return j

    def complete(self, job_id: str) -> Optional[Job]:
        j = self.get(job_id)
        if j:
            j.status = "completed"
            self._save(j)
        return j

    # ── Progress ──────────────────────────────────────────────────────────────

    def checkpoint(self, job: Job, item_id: str, success: bool = True) -> None:
        """Record progress on one unit. Call after each item in a batch."""
        if success:
            job.completed_units += 1
            job.checkpoint = item_id
        else:
            job.failed_units += 1
        self._save(job)

    def should_report_progress(self, job: Job) -> bool:
        """True every 10 completed batches."""
        if job.batch_size <= 0 or job.completed_units <= 0:
            return False
        batches_done = job.completed_units // job.batch_size
        return batches_done > 0 and (job.completed_units % (job.batch_size * 10)) == 0

    # ── Async background execution (G4 / #27) ─────────────────────────────────

    def submit_background(
        self,
        fn: Callable[[], str],
        title: str,
        completions_queue: deque,
        job_id: Optional[str] = None,
        thread_id: str = "",
    ) -> str:
        """
        Run `fn` in a daemon thread. When it completes, push
        {"job_id": ..., "title": ..., "result": ...} onto completions_queue.

        Returns the job_id (8-char UUID prefix).
        If job_id is provided it must already exist in self._jobs; otherwise a
        new Job record is created automatically.

        The caller owns completions_queue — typically IgorAgent._job_completions.
        """
        if job_id is None:
            job = self.create(title=title)
            job_id = job.job_id
        else:
            job = self.get(job_id)
            if job is None:
                job = self.create(title=title)
                job_id = job.job_id

        if thread_id:
            job.thread_id = thread_id
            self._save(job)
        self.start(job_id)

        def _worker():
            try:
                result = fn()
            except Exception as exc:
                result = f"[ERROR] {exc}"
            self.complete(job_id)
            completions_queue.append(
                {
                    "job_id": job_id,
                    "title": title,
                    "result": result,
                    "thread_id": thread_id,
                }
            )

        t = threading.Thread(target=_worker, daemon=True, name=f"igor-job-{job_id}")
        t.start()
        return job_id
