"""RSS feed and article models — SQLite-backed."""

import json
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Boolean, Float, Text, String, DateTime, ForeignKey
from sqlalchemy import select, func

from models.base import Base, async_session
from config import settings


class RSSFeed(Base):
    __tablename__ = "rss_feeds"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    url = Column(String(500), nullable=False)
    enabled = Column(Boolean, default=True)
    last_fetched = Column(DateTime, nullable=True)
    error_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RSSArticle(Base):
    __tablename__ = "rss_articles"

    id = Column(Integer, primary_key=True)
    feed_id = Column(Integer, ForeignKey("rss_feeds.id"), nullable=False)
    title = Column(String(500), nullable=False)
    url = Column(String(1000), nullable=False, unique=True)
    summary = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    ingested = Column(Boolean, default=False)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FeedStore:
    """CRUD for RSS feeds and articles."""

    async def init_feeds(self, feeds: dict[str, str]):
        """Seed configured feeds into DB. Does not overwrite existing."""
        async with async_session() as db:
            for name, url in feeds.items():
                existing = (await db.execute(
                    select(RSSFeed).where(RSSFeed.name == name)
                )).scalar_one_or_none()
                if not existing:
                    db.add(RSSFeed(name=name, url=url))
            await db.commit()

    async def list_feeds(self) -> list[dict]:
        async with async_session() as db:
            # Single query for article counts per feed
            count_rows = (await db.execute(
                select(RSSArticle.feed_id, func.count().label("cnt"))
                .group_by(RSSArticle.feed_id)
            )).all()
            count_map = {row.feed_id: row.cnt for row in count_rows}

            rows = (await db.execute(
                select(RSSFeed).order_by(RSSFeed.name)
            )).scalars().all()

            return [{
                "id": f.id,
                "name": f.name,
                "url": f.url,
                "enabled": f.enabled,
                "last_fetched": f.last_fetched.isoformat() if f.last_fetched else None,
                "error_count": f.error_count,
                "article_count": count_map.get(f.id, 0),
            } for f in rows]

    async def get_feed_by_name(self, name: str) -> RSSFeed | None:
        async with async_session() as db:
            return (await db.execute(
                select(RSSFeed).where(RSSFeed.name == name)
            )).scalar_one_or_none()

    async def update_feed_fetched(self, feed_id: int, error: bool = False):
        async with async_session() as db:
            feed = (await db.execute(
                select(RSSFeed).where(RSSFeed.id == feed_id)
            )).scalar_one_or_none()
            if feed:
                feed.last_fetched = datetime.now(timezone.utc)
                if error:
                    feed.error_count += 1
                    if feed.error_count >= settings.rss_disable_after_errors:
                        feed.enabled = False
                else:
                    feed.error_count = 0
                await db.commit()

    async def save_article(self, feed_id: int, title: str, url: str,
                          summary: str = "", published_at: datetime | None = None) -> bool:
        """Save article, return True if new (not duplicate)."""
        async with async_session() as db:
            existing = (await db.execute(
                select(RSSArticle).where(RSSArticle.url == url)
            )).scalar_one_or_none()
            if existing:
                return False
            article = RSSArticle(
                feed_id=feed_id, title=title, url=url,
                summary=summary[:2000] if summary else None,
                published_at=published_at,
            )
            db.add(article)
            await db.commit()
            return True

    async def get_uningested(self, limit: int = 20) -> list[RSSArticle]:
        async with async_session() as db:
            return list((await db.execute(
                select(RSSArticle)
                .where(RSSArticle.ingested == False)  # noqa: E712
                .where(RSSArticle.retry_count < settings.rss_max_retries)
                .order_by(RSSArticle.published_at.desc().nullslast())
                .limit(limit)
            )).scalars().all())

    async def mark_ingested(self, article_id: int):
        async with async_session() as db:
            article = (await db.execute(
                select(RSSArticle).where(RSSArticle.id == article_id)
            )).scalar_one_or_none()
            if article:
                article.ingested = True
                await db.commit()

    async def list_articles(self, feed_name: str = "", limit: int = 20) -> list[dict]:
        async with async_session() as db:
            # Load all feeds in one query
            all_feeds = (await db.execute(select(RSSFeed))).scalars().all()
            feed_map = {f.id: f.name for f in all_feeds}

            query = select(RSSArticle).order_by(RSSArticle.published_at.desc().nullslast())
            if feed_name:
                feed = next((f for f in all_feeds if f.name == feed_name), None)
                if feed:
                    query = query.where(RSSArticle.feed_id == feed.id)
            query = query.limit(limit)

            rows = (await db.execute(query)).scalars().all()

            return [{
                "id": a.id,
                "feed": feed_map.get(a.feed_id, "?"),
                "title": a.title,
                "url": a.url,
                "summary": (a.summary or "")[:200],
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "ingested": a.ingested,
            } for a in rows]

    async def mark_ingest_failed(self, article_id: int) -> bool:
        """Increment retry count. Returns True if should retry, False if permanently failed."""
        async with async_session() as db:
            article = (await db.execute(
                select(RSSArticle).where(RSSArticle.id == article_id)
            )).scalar_one_or_none()
            if article:
                article.retry_count = (article.retry_count or 0) + 1
                if article.retry_count >= settings.rss_max_retries:
                    article.ingested = True  # stop retrying
                    await db.commit()
                    return False
                await db.commit()
            return True
