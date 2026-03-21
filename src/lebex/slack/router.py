from collections.abc import Callable

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from lebane.client import LebaneClient
from lebex.api.dependencies import get_checkpointer
from lebex.api.dependencies import get_db_async_sessionmaker
from lebex.api.dependencies import get_lebane_async_sessionmaker
from lebex.api.dependencies import get_lebane_db_async_sessionmaker
from lebex.api.dependencies import get_memory_store
from lebex.api.dependencies import get_settings
from lebex.core.settings import Settings

from .app import app


app_handler = AsyncSlackRequestHandler(app)

router = APIRouter(prefix="/slack", tags=["slack"])


@router.post("/events")
async def handle_events(
    request: Request,
    dbsessionmaker: async_sessionmaker[AsyncSession] = Depends(
        get_db_async_sessionmaker
    ),
    checkpointer: BaseCheckpointSaver = Depends(get_checkpointer),
    store: BaseStore = Depends(get_memory_store),
    lsessionmaker: Callable[[str], LebaneClient] = Depends(
        get_lebane_async_sessionmaker
    ),
    ldbsessionmaker: async_sessionmaker[AsyncSession] = Depends(
        get_lebane_db_async_sessionmaker
    ),
    settings: Settings = Depends(get_settings),
):
    return await app_handler.handle(
        request,
        addition_context_properties={
            "dbsessionmaker": dbsessionmaker,
            "checkpointer": checkpointer,
            "store": store,
            "lsessionmaker": lsessionmaker,
            "ldbsessionmaker": ldbsessionmaker,
            "settings": settings,
        },
    )
