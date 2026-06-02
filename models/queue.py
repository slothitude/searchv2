"""Persistent search job queue — SQLite-backed."""

import json
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Float, Text, String, DateTime
from sqlalchemy import select, update

from models.base import Base, async_session


class SearchJob(Base):
    __tablename__ = "search_jobs"

    id = Column(Integer, primary_key=True)
    job_type = Column(String(20), nullable=False)  # "search" | "ingest"
    query = Column(Text, nullable=False)
    params = Column(Text, default="{}")  # JSON: max_urls, classify, etc.
    status = Column(String(20), default="pending")  # pending|running|done|failed
    priority = Column(Float, default=0.5)
    result = Column(Text, nullable=True)  # JSON
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class QueueStore:
    """CRUD for search jobs."""

    async def enqueue(self, job_type: str, query: str, params: dict | None = None,
                      priority: float = 0.5) -> SearchJob:
        async with async_session() as db:
            job = SearchJob(
                job_type=job_type, query=query,
                params=json.dumps(params or {}),
                priority=priority,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job

    async def dequeue_next(self) -> SearchJob | None:
        """Grab highest-priority pending job and mark it running."""
        async with async_session() as db:
            job = (await db.execute(
                select(SearchJob)
                .where(SearchJob.status == "pending")
                .order_by(SearchJob.priority.desc(), SearchJob.created_at)
                .limit(1)
            )).scalar_one_or_none()
            if not job:
                return None
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(job)
            return job

    async def update_status(self, job_id: int, status: str,
                            result: dict | None = None, error: str | None = None):
        async with async_session() as db:
            values = {"status": status, "finished_at": datetime.now(timezone.utc)}
            if result is not None:
                values["result"] = json.dumps(result, default=str)
            if error is not None:
                values["error"] = error
            await db.execute(
                update(SearchJob).where(SearchJob.id == job_id).values(**values)
            )
            await db.commit()

    async def get_job(self, job_id: int) -> SearchJob | None:
        async with async_session() as db:
            return (await db.execute(
                select(SearchJob).where(SearchJob.id == job_id)
            )).scalar_one_or_none()

    async def list_jobs(self, limit: int = 50) -> list[dict]:
        async with async_session() as db:
            rows = (await db.execute(
                select(SearchJob)
                .order_by(SearchJob.created_at.desc())
                .limit(limit)
            )).scalars().all()
            return [_job_to_dict(j) for j in rows]

    async def cancel_job(self, job_id: int) -> bool:
        async with async_session() as db:
            job = (await db.execute(
                select(SearchJob).where(SearchJob.id == job_id)
            )).scalar_one_or_none()
            if not job or job.status not in ("pending",):
                return False
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            await db.commit()
            return True

    async def reset_running(self):
        """Reset any 'running' jobs back to 'pending' (crash recovery)."""
        async with async_session() as db:
            result = await db.execute(
                update(SearchJob)
                .where(SearchJob.status == "running")
                .values(status="pending", started_at=None)
            )
            await db.commit()
            return result.rowcount


def _job_to_dict(job: SearchJob) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "query": job.query,
        "params": json.loads(job.params) if job.params else {},
        "status": job.status,
        "priority": job.priority,
        "result": json.loads(job.result) if job.result else None,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
