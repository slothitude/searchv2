"""RSS feed fetcher — periodic polling and ingest into knowledge graph."""

import asyncio
import logging
import re
from datetime import datetime, timezone

import feedparser
from sqlalchemy import select

from config import settings
from models.rss import RSSFeed, RSSArticle, FeedStore
from models.base import async_session
from core.extractor import fetch_and_extract
from core.ingest import _find_working_model, _extract_knowledge, _clean_entity_name
from core.knowledge import KnowledgeGraph, MemoryStore
from models.queue import QueueStore

log = logging.getLogger("searchv2.rss")

store = FeedStore()

# Cache the working model across articles in a single batch
_cached_model: str | None = None


def _parse_published(entry) -> datetime | None:
    """Parse published/updated date from a feedparser entry."""
    raw = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not raw:
        return None
    try:
        # struct_time -> datetime (entry.published_parsed is a time.struct_time)
        return datetime.fromtimestamp(
            int(feedparser._parse_date(raw).timestamp()), tz=timezone.utc
        )
    except Exception:
        return None


async def fetch_feed(feed_url: str) -> list[dict]:
    """Parse an RSS feed and return list of article dicts."""
    try:
        def _parse():
            return feedparser.parse(feed_url)

        parsed = await asyncio.get_event_loop().run_in_executor(None, _parse)
        articles = []
        for entry in parsed.entries:
            articles.append({
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", "").strip(),
                "summary": entry.get("summary", "") or entry.get("description", ""),
                "published_at": _parse_published(entry),
            })
        return articles
    except Exception as e:
        log.error("Failed to parse feed %s: %s", feed_url, e)
        return []


async def fetch_all() -> dict:
    """Fetch all enabled feeds, save new articles. Returns summary."""
    async with async_session() as db:
        feeds = (await db.execute(
            select(RSSFeed).where(RSSFeed.enabled == True)  # noqa: E712
        )).scalars().all()

    total_new = 0
    results = []
    # Fetch feeds concurrently
    feed_articles = await asyncio.gather(
        *[fetch_feed(f.url) for f in feeds], return_exceptions=True,
    )
    for feed, outcome in zip(feeds, feed_articles):
        if isinstance(outcome, Exception):
            await store.update_feed_fetched(feed.id, error=True)
            log.error("Error fetching feed '%s': %s", feed.name, outcome)
            results.append({"feed": feed.name, "error": str(outcome)})
            continue

        articles = outcome
        new_count = 0
        for a in articles:
            if a.get("title") and a.get("url"):
                is_new = await store.save_article(
                    feed.id, a["title"], a["url"],
                    summary=a.get("summary", ""),
                    published_at=a.get("published_at"),
                )
                if is_new:
                    new_count += 1
        await store.update_feed_fetched(feed.id, error=False)
        total_new += new_count
        results.append({"feed": feed.name, "total": len(articles), "new": new_count})
        log.info("Feed '%s': %d articles, %d new", feed.name, len(articles), new_count)

    return {"feeds_processed": len(results), "total_new": total_new, "details": results}


def set_model_cache(model: str | None):
    """Set/clear the cached working model for batch processing."""
    global _cached_model
    _cached_model = model


async def ingest_article(article: RSSArticle) -> bool:
    """Extract text from article URL and run through ingest pipeline."""
    from core.retriever import ContextRetriever
    from models.knowledge import Entity

    try:
        content = await fetch_and_extract(article.url)
        if "error" in content or not content.get("text", "").strip():
            log.warning("Could not extract content from %s", article.url)
            return False

        text = content["text"]
        url = content["url"]

        async with async_session() as db:
            kg = KnowledgeGraph(db)
            ms = MemoryStore(db)
            retriever = ContextRetriever(db)

            # Use cached model or probe once
            model = _cached_model
            if model is None:
                model = await _find_working_model(timeout=10)
                set_model_cache(model)

            if model:
                knowledge = await _extract_knowledge(text, url, model=model)
            else:
                knowledge = [{
                    "entity": article.title,
                    "type": "news",
                    "claims": [
                        {"key": "source", "value": url, "confidence": 1.0},
                        {"key": "summary", "value": text[:500], "confidence": 0.5},
                    ],
                }]

            ingested_entities = []
            for item in knowledge:
                entity_name = _clean_entity_name(item.get("entity", ""), url=url)
                entity_type = item.get("type", "document")
                claims = item.get("claims", [])
                if not entity_name:
                    continue

                description = f"RSS article: {article.title[:100]}"
                entity = await kg.add_entity(entity_name, entity_type=entity_type, description=description)

                for c in claims:
                    key = c.get("key", "").strip()
                    value = c.get("value", "").strip()
                    conf = c.get("confidence", 0.5)
                    if not key or not value or len(key) < 3 or len(value) < 10:
                        continue
                    await kg.add_claim(
                        entity_name=entity_name, claim_type="rss",
                        claim_key=key, claim_value=str(value)[:2000],
                        confidence=max(0.1, min(1.0, conf)),
                    )

                # Index embeddings for each entity (not just the last)
                r = await db.execute(select(Entity).where(Entity.name == entity_name).limit(1))
                ent = r.scalar_one_or_none()
                if ent:
                    if not await retriever.has_embedding("entity", ent.id):
                        await retriever.index_entity(ent.id)
                    await retriever.index_claims_for_entity(ent.id)

                ingested_entities.append(entity_name)

            if ingested_entities:
                await ms.add_memory(
                    content=f"RSS ingest: {article.title}",
                    context=f"feed: {article.url}",
                    related_entities=ingested_entities,
                    importance=0.5,
                )
            await db.commit()

        await store.mark_ingested(article.id)
        log.info("Ingested RSS article: %s", article.title[:80])
        return True
    except Exception as e:
        log.error("Failed to ingest article %s: %s", article.url, e)
        return False


async def ingest_uningested(limit: int = 10) -> dict:
    """Ingest all uningested articles through the knowledge pipeline."""
    articles = await store.get_uningested(limit)
    ingested = 0
    failed = 0
    for article in articles:
        ok = await ingest_article(article)
        if ok:
            ingested += 1
        else:
            failed += 1
    return {"ingested": ingested, "failed": failed, "total": len(articles)}


class RSSPoller:
    """Periodic RSS polling loop."""

    def __init__(self):
        self._task: asyncio.Task | None = None

    async def start(self):
        await store.init_feeds(settings.rss_feeds)
        self._task = asyncio.create_task(self._loop())
        log.info("RSS poller started (interval %ds)", settings.rss_poll_interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("RSS poller stopped")

    async def _loop(self):
        await asyncio.sleep(5)
        queue = QueueStore()
        while True:
            try:
                summary = await fetch_all()
                log.info("RSS poll: %d feeds, %d new articles",
                         summary.get("feeds_processed", 0),
                         summary.get("total_new", 0))

                if summary.get("total_new", 0) > 0:
                    articles = await store.get_uningested(limit=10)
                    enqueued = 0
                    for article in articles:
                        await queue.enqueue(
                            "rss_ingest",
                            f"RSS: {article.title[:80]}",
                            params={"article_id": article.id, "url": article.url,
                                    "title": article.title},
                            priority=0.3,
                        )
                        enqueued += 1
                    log.info("RSS: enqueued %d articles for ingest", enqueued)
                    await self._enqueue_followup_searches(articles, queue)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("RSS poll loop error: %s", e, exc_info=True)

            await asyncio.sleep(settings.rss_poll_interval)

    async def _enqueue_followup_searches(self, articles, queue):
        """Extract interesting topics from article titles and queue search jobs."""
        topics_seen = set()
        for article in articles:
            title = article.title
            candidates = set()
            for m in re.finditer(r'"([^"]+)"', title):
                candidates.add(m.group(1).strip())
            for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b', title):
                phrase = m.group(1)
                if len(phrase) >= 5 and phrase.lower() not in (
                    "abc news", "bbc news", "guardian australia", "google news",
                    "sky news", "sbs news", "news com", "perth now",
                ):
                    candidates.add(phrase)

            for topic in candidates:
                if topic in topics_seen:
                    continue
                topics_seen.add(topic)
                query = f"{topic} Australia 2026"
                await queue.enqueue(
                    "rss_search",
                    f"RSS follow-up: {topic}",
                    params={"query": query, "max_urls": 2},
                    priority=0.2,
                )
                log.info("RSS self-learning: queued search for '%s'", topic)


poller = RSSPoller()
