from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine

from .settings import Settings


def get_async_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        str(settings.DB_URI),
        pool_size=10,
        max_overflow=5,
        echo=settings.DB_DEBUG,
    )

# DB_URI - postgres local
def get_async_sessionmaker(
    settings: Settings,
) -> async_sessionmaker[AsyncSession]:
    engine = get_async_engine(settings)
    return async_sessionmaker(engine, expire_on_commit=False)


# CHECKPOINT_DB_URI - postgres local
def get_async_checkpoint_pg_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=str(settings.CHECKPOINT_DB_URI),
        min_size=1,
        max_size=10,
    )


# STORE_DB_URI - postgres local
def get_async_store_pg_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=str(settings.STORE_DB_URI),
        min_size=1,
        max_size=10,
    )
