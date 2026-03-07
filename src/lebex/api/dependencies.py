from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable

from fastapi import Request
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from lebane.client import LebaneClient
from lebex.core.settings import Settings


def get_settings(request: Request) -> Settings:
    return request.state.settings


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async_sessionmaker = request.state.async_dbsessionmaker
    async with async_sessionmaker() as session:
        yield session


async def get_db_async_sessionmaker(
    request: Request,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield request.state.async_dbsessionmaker


async def get_checkpointer(request: Request) -> AsyncPostgresSaver:
    async_pg_pool = request.state.async_checkpoint_pg_pool
    return AsyncPostgresSaver(conn=async_pg_pool)


async def get_memory_store(request: Request) -> AsyncPostgresStore:
    async_pg_pool = request.state.async_store_pg_pool
    return AsyncPostgresStore(conn=async_pg_pool)


async def get_lebane_async_sessionmaker(
    request: Request,
) -> AsyncIterator[Callable[[str], Awaitable[LebaneClient]]]:
    yield request.state.async_lsessionmaker


async def get_lebane_db_async_sessionmaker(
    request: Request,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield request.state.async_ldbsessionmaker


async def get_model_manager(request: Request):
    yield request.state.model_manager
