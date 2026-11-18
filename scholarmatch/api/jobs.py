"""Background match runs. A live run can take 10–15 minutes, so the API starts it in a worker thread and the front
end follows its progress (poll `GET /api/jobs/{id}` or stream `GET /api/jobs/{id}/events`).

Jobs live in memory only: the finished `MatchRun` is saved to the DB by the Supervisor, so a restart loses nothing
but the progress of a run that was still going."""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..agents.supervisor import Supervisor
from ..models import StudentProfile
from .schemas import Job

MAX_FINISHED_JOBS = 200          # keep recent finished jobs so a late poll still sees the result


class JobRunner:
    def __init__(self, make_supervisor: Callable[[], Supervisor], workers: int = 2):
        self._make_supervisor = make_supervisor
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="match-run")
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def start(self, p: StudentProfile, force: bool, as_of: str | None = None) -> Job:
        """Start a run for `p`, or return the one already queued or running for that profile."""
        with self._lock:
            for job in self._jobs.values():
                if job.profile_id == p.profile_id and job.state in ("queued", "running"):
                    return job.model_copy()
            job = Job(job_id=uuid.uuid4().hex[:12], profile_id=p.profile_id, state="queued", message="Waiting to start…")
            self._jobs[job.job_id] = job
            self._prune()
        self._pool.submit(self._run, job.job_id, p, force, as_of)
        return job.model_copy()

    def active(self, profile_id: str) -> bool:
        """A run for this profile is queued or running."""
        with self._lock:
            return any(j.profile_id == profile_id and j.state in ("queued", "running") for j in self._jobs.values())

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy() if job else None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs[job_id]
            self._jobs[job_id] = job.model_copy(update=fields)

    def _run(self, job_id: str, p: StudentProfile, force: bool, as_of: str | None) -> None:
        self._update(job_id, state="running", message="Starting…")
        try:
            run = self._make_supervisor().run(
                p, as_of=as_of, force=force,
                progress=lambda msg, frac: self._update(job_id, message=msg, fraction=min(1.0, max(0.0, frac))))
        except Exception as e:                    # the Supervisor already falls back per item; this is a whole-run fault
            self._update(job_id, state="error", error=f"{type(e).__name__}: {e}", message="The run failed.")
            return
        self._update(job_id, state="done", fraction=1.0, message="Done.", run_id=run.run_id)

    def _prune(self) -> None:
        finished = [j for j in self._jobs.values() if j.state in ("done", "error")]
        for j in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
            del self._jobs[j.job_id]
