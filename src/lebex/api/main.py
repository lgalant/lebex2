
 
from .router import router

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.store.postgres import AsyncPostgresStore

from lebex.core.database import get_async_checkpoint_pg_pool
from lebex.core.database import get_async_sessionmaker
from lebex.core.database import get_async_store_pg_pool
from lebex.core.lebane import get_lebane_async_sessionmaker
from lebex.core.lebane import get_lebane_db_async_sessionmaker
from lebex.core.settings import get_settings

from .private.app import app as app_private

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[dict[str, Any]]:
    settings = get_settings()
    async_dbsessionmaker = get_async_sessionmaker(settings=settings)
    async_checkpoint_pg_pool = get_async_checkpoint_pg_pool(settings=settings)
    async_store_pg_pool = get_async_store_pg_pool(settings=settings)
    async_lsessionmaker = get_lebane_async_sessionmaker(settings=settings)
    async_ldbsessionmaker = get_lebane_db_async_sessionmaker(settings=settings)

    async with async_checkpoint_pg_pool, async_store_pg_pool:
        async with async_store_pg_pool.connection() as conn:
            await conn.set_autocommit(True)
            store = AsyncPostgresStore(conn=conn)
            await store.setup()

        yield {
            "settings": settings,
            "async_checkpoint_pg_pool": async_checkpoint_pg_pool,
            "async_store_pg_pool": async_store_pg_pool,
            "async_dbsessionmaker": async_dbsessionmaker,
            "async_lsessionmaker": async_lsessionmaker,
            "async_ldbsessionmaker": async_ldbsessionmaker,
        }

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000"
    ],  # or ["*"] to allow all (not recommended in prod)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router=router)
app.mount(path="/private/api", app=app_private, name="private")