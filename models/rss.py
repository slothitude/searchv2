"""RSS feed and article models — SQLite-backed."""

import json
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Boolean, Float, Text, String, DateTime, ForeignKey
from sqlalchemy import select, func

from models.base import Base, async_session


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
            rows = (await db.execute(
                select(RSSFeed).order_by(RSSFeed.name)
            )).scalars().all()

            result = []
            for f in rows:
                count = (await db.execute(
                    select(func.count()).select_from(RSSArticle)
                    .where(RSSArticle.feed_id == f.id)
                )).scalar()
                result.append({
                    "id": f.id,
                    "name": f.name,
                    "url": f.url,
                    "enabled": f.enabled,
                    "last_fetched": f.last_fetched.isoformat() if f.last_fetched else None,
                    "error_count": f.error_count,
                    "article_count": count,
                })
            return result

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
            query = select(RSSArticle).order_by(RSSArticle.published_at.desc().nullslast())
            if feed_name:
                feed = (await db.execute(
                    select(RSSFeed).where(RSSFeed.name == feed_name)
                )).scalar_one_or_none()
                if feed:
                    query = query.where(RSSArticle.feed_id == feed.id)
            query = query.limit(limit)

            rows = (await db.execute(query)).scalars().all()
            # Get feed names
            feed_ids = {r.feed_id for r in rows}
            feeds = {}
            for fid in feed_ids:
                f = (await db.execute(
                    select(RSSFeed).where(RSSFeed.id == fid)
                )).scalar_one_or_none()
                if f:
                    feeds[fid] = f.name

            return [{
                "id": a.id,
                "feed": feeds.get(a.feed_id, "?"),
                "title": a.title,
                "url": a.url,
                "summary": (a.summary or "")[:200],
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "ingested": a.ingested,
            } for a in rows]
