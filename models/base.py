from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from config import settings

_db_path = settings.data_dir / "searchv2.db"
_db_path.parent.mkdir(parents=True, exist_ok=True)
engine = create_async_engine(
    f"sqlite+aiosqlite:///{_db_path}", echo=settings.debug,
    connect_args={"timeout": 30},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migration: add retry_count to rss_articles if missing
        try:
            await conn.execute(text("ALTER TABLE rss_articles ADD COLUMN retry_count INTEGER DEFAULT 0"))
        except Exception:
            pass  # column already exists
