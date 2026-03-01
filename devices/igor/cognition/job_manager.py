"""
Job Manager — persistent state for long-running multi-step tasks.

Long-running jobs are a different class from queries:
  - They span multiple turns and may survive restarts.
  - Progress is checkpointed after each batch so work is never lost.
  - Failed units are logged and skipped; the job continues.
  - On completion, the associated GitHub work order can be closed.

Storage: ~/.TheIgors/jobs/{job_id}.json
Loaded on boot — pending/running/paused jobs resume automatically.

Trigger (called from main._process()):
  complexity.score > 0.6 AND complexity.is_multi_unit == True
  → Igor confirms "this is a long job, creating job #N"
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

JOBS_DIR = Path.home() / ".TheIgors" / "jobs"

_STATUS_ACTIVE = frozenset({"pending", "running", "paused"})


@dataclass
class Job:
    job_id: str
    title: str
    status: str           # pending | running | paused | completed | cancelled | failed
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


class JobManager:
    """
    Manages the lifecycle of long-running jobs.
    Loaded at Igor boot; persists state to ~/.TheIgors/jobs/.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._load_active()

    # ── Boot ──────────────────────────────────────────────────────────────────

    def _load_active(self) -> None:
        """Load pending/running/paused jobs from disk on startup."""
        if not JOBS_DIR.exists():
            return
        for path in sorted(JOBS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                j = Job(**data)
                if j.status in _STATUS_ACTIVE:
                    self._jobs[j.job_id] = j
            except Exception:
                pass

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
                    except Exception:
                        pass
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
