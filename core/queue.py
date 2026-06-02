"""Background queue worker — processes search/ingest jobs sequentially."""

import asyncio
import json
import logging

from core.events import bus
from core.search import search_searxng
from core.ingest import ingest
from core.rss import ingest_article
from models.queue import QueueStore
from models.rss import RSSArticle
from sqlalchemy import select
from models.base import async_session
from config import settings

log = logging.getLogger("searchv2.queue")


class QueueWorker:
    """Processes jobs from the persistent queue one at a time."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self.store = QueueStore()

    async def start(self):
        if not settings.queue_enabled:
            log.info("Queue disabled by config")
            return
        # Crash recovery: reset any stranded running jobs
        reset_count = await self.store.reset_running()
        if reset_count:
            log.warning("Reset %d stranded running jobs to pending", reset_count)
        self._task = asyncio.create_task(self._loop())
        log.info("Queue worker started (poll interval %.1fs)", settings.queue_poll_interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("Queue worker stopped")

    async def _loop(self):
        while True:
            try:
                job = await self.store.dequeue_next()
                if job:
                    await self._dispatch(job)
                else:
                    await asyncio.sleep(settings.queue_poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Queue loop error: %s", e, exc_info=True)
                await asyncio.sleep(settings.queue_poll_interval)

    async def _dispatch(self, job):
        log.info("Processing job #%d: %s — %s", job.id, job.job_type, job.query)
        await bus.publish("queue.job_started", {
            "job_id": job.id, "job_type": job.job_type, "query": job.query,
        })

        try:
            params = json.loads(job.params) if job.params else {}

            if job.job_type == "search":
                result = await search_searxng(
                    job.query,
                    categories=params.get("categories", "general"),
                    max_results=params.get("max_results", 10),
                )
                await self.store.update_status(job.id, "done", result={"results": result})

            elif job.job_type == "ingest":
                async with async_session() as db:
                    result = await ingest(
                        db=db,
                        query=job.query,
                        max_urls=params.get("max_urls", 3),
                        classify=params.get("classify", True),
                        index_embeddings=params.get("index_embeddings", True),
                    )
                    await db.commit()
                await self.store.update_status(job.id, "done", result=result)

            elif job.job_type == "rss_ingest":
                # Ingest a single RSS article by URL
                article_id = params.get("article_id")
                url = params.get("url")
                async with async_session() as db:
                    if article_id:
                        article = (await db.execute(
                            select(RSSArticle).where(RSSArticle.id == article_id)
                        )).scalar_one_or_none()
                    elif url:
                        article = (await db.execute(
                            select(RSSArticle).where(RSSArticle.url == url)
                        )).scalar_one_or_none()
                    else:
                        article = None
                if article:
                    ok = await ingest_article(article)
                    await self.store.update_status(job.id, "done",
                                                  result={"ingested": ok, "title": job.query})
                else:
                    await self.store.update_status(job.id, "done",
                                                  result={"skipped": True, "reason": "article not found"})

            elif job.job_type == "rss_search":
                # Self-learning follow-up search triggered by RSS
                query = params.get("query", job.query)
                async with async_session() as db:
                    result = await ingest(
                        db=db,
                        query=query,
                        max_urls=params.get("max_urls", 2),
                        classify=True,
                        index_embeddings=True,
                    )
                    await db.commit()
                await self.store.update_status(job.id, "done", result=result)

            else:
                await self.store.update_status(job.id, "failed",
                                              error=f"Unknown job type: {job.job_type}")
                return

            log.info("Job #%d completed", job.id)
            await bus.publish("queue.job_completed", {
                "job_id": job.id, "job_type": job.job_type,
            })

        except Exception as e:
            log.error("Job #%d failed: %s", job.id, e, exc_info=True)
            await self.store.update_status(job.id, "failed", error=str(e))
            await bus.publish("queue.job_failed", {
                "job_id": job.id, "error": str(e),
            })


worker = QueueWorker()
