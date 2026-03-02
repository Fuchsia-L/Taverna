import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _resolve_database_url() -> str:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url:
        return db_url
    return "postgresql+asyncpg://taverna:taverna@localhost:5432/taverna"


DATABASE_URL = _resolve_database_url()
engine = create_async_engine(DATABASE_URL, future=True, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
