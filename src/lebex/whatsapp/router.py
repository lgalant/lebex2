import datetime
import logging
import uuid
from collections.abc import Awaitable
from collections.abc import Callable

import httpx
from fastapi import APIRouter
from fastapi import Depends
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from lebex.app.main import aanswer
from lebex.core.models import LebexMessageInDB


from lebane.client import LebaneClient

from lebex.api.dependencies import get_checkpointer
from lebex.api.dependencies import get_db_async_sessionmaker
from lebex.api.dependencies import get_lebane_async_sessionmaker
from lebex.api.dependencies import get_lebane_db_async_sessionmaker
from lebex.api.dependencies import get_memory_store
from lebex.api.dependencies import get_settings


from lebex.core.settings import Settings

from .schemas.ioniksend_message import IoniksendMessage


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

 
async def save_lebex_message(
    content: str,
    sender: str,
    receiver: str,
    thread_id: str,
    db_session: AsyncSession,
):
    try:
        unique_id = str(uuid.uuid4())
        lebex_message_in_db = LebexMessageInDB(
            version=1,
            platform="SLACK",
            ocurred_at=datetime.datetime.now(tz=datetime.UTC),
            unique_id=unique_id,
            thread_id=thread_id,
            sender=sender,
            receiver=receiver,
            content=content,
            created_by="lebex",
            updated_by="lebex",
        )
        async with db_session.begin():
            db_session.add(lebex_message_in_db)
    except Exception:
        logger.warn(
            "Failed to save LebexMessage with unique_id of %s",
            unique_id,
            exc_info=True,
        )
 
 
# db_session - DB_URI
async def save_lebex_message_from_ioniksend(
    event: IoniksendMessage, db_session: AsyncSession
) -> None:
    try:
        lebex_message_in_db = LebexMessageInDB(
            version=1,
            platform="WHATSAPP",
            ocurred_at=datetime.datetime.now(tz=datetime.UTC),
            unique_id=event.unique_id,
            thread_id=event.client_num,
            sender=event.client_num,
            receiver=event.chatbot_num,
            content=event.body,
            created_by="lebex",
            updated_by="lebex",
        )
        async with db_session.begin():
            db_session.add(lebex_message_in_db)
    except Exception:
        logger.warn(
            "Failed to save LebexMessage with unique_id of %s",
            event.unique_id,
            exc_info=True,
        )
 
async def send_message(
    text: str,
    phone: str,
    settings: Settings,
    sender: str,
    dbsessionmaker: async_sessionmaker[AsyncSession],
    file: None | str = None,
) -> None:
    print(f"Sending message to {phone}: {text}")
    params = {
        "sendMessageRealTime": "",
        "text": text,
        "type": "whatsapp",
        "from": "lebane",
        "to": phone,
        "apikey": settings.IONIKSEND_APIKEY,
        "apitoken": settings.IONIKSEND_APITOKEN,
        "route": settings.IONIKSEND_ROUTE,
        "chatbot": "142",
        "view_on_chat": "1",
    }
    if file:
        params["file"] = file
    async with httpx.AsyncClient() as ioniksend_client:
        await ioniksend_client.get(
            url="https://app.ioniksend.com/ionikAPI",
            params=params,
        )

    async with dbsessionmaker() as db_session:
        await save_lebex_message(
            content=text,
            sender=sender,
            receiver=phone,
            thread_id=phone,
            db_session=db_session,
        )

'''payload: IoniksendMessage — del body HTTP
FastAPI detecta que IoniksendMessage es un modelo Pydantic (no un tipo primitivo, no tiene Depends). 
Por eso lo trata como request body JSON. Cuando llega un POST a /whatsapp/events, FastAPI deserializa 
automáticamente el JSON del body en un objeto IoniksendMessage.

= Depends(...) — inyección de dependencias
Depends le dice a FastAPI: "no lo saques del request, sino ejecutá esta función y pasame lo que retorna".

dbsessionmaker: async_sessionmaker[AsyncSession] = Depends(get_db_async_sessionmaker)
FastAPI llama a get_db_async_sessionmaker() antes de ejecutar handle_events, y el resultado se inyecta 
como dbsessionmaker. asi con todos

'''
@router.post("/events")
async def handle_events(
    payload: IoniksendMessage,
    dbsessionmaker: async_sessionmaker[AsyncSession] = Depends(get_db_async_sessionmaker), # DB_URI
    checkpointer: BaseCheckpointSaver = Depends(get_checkpointer), # CHECKPOINT_DB_URI
    store: BaseStore = Depends(get_memory_store), # STORE_DB_URI
    lsessionmaker: Callable[[str], Awaitable[LebaneClient]] = Depends(get_lebane_async_sessionmaker), # LEBANE_BASE_URL
    ldbsessionmaker: async_sessionmaker[AsyncSession] = Depends(get_lebane_db_async_sessionmaker), # LEBANE_DB_URI
    settings: Settings = Depends(get_settings),
):
    async with dbsessionmaker() as db_session:
        await save_lebex_message_from_ioniksend(
            event=payload, db_session=db_session
        )

    phone = payload.client_num
    try:
        _lsessionmaker = await lsessionmaker(phone)
    except httpx.HTTPStatusError:
        logger.warning(
            "Authentication to Lebane has failed for phone: %s", phone
        )
        return
 
    
    response = await aanswer(
        text=payload.body,
        configurable={
            "thread_id": phone,
            "dbsessionmaker": dbsessionmaker,
            "checkpointer": checkpointer,
            "store": store,
            "lsessionmaker": _lsessionmaker,
            "ldbsessionmaker": ldbsessionmaker,
            "settings": settings,
        },
    )
 
    if isinstance(response, str):
        await send_message(
            text=response,
            phone=phone,
            settings=settings,
            sender=payload.chatbot_num,
            dbsessionmaker=dbsessionmaker,
        )
    elif isinstance(response, list):
        for message in response:
            if isinstance(message, str):
                await send_message(
                    text=message,
                    phone=phone,
                    settings=settings,
                    sender=payload.chatbot_num,
                    dbsessionmaker=dbsessionmaker,
                )
            elif isinstance(message, dict):
                await send_message(
                    text=message["text"],
                    phone=phone,
                    settings=settings,
                    sender=payload.chatbot_num,
                    dbsessionmaker=dbsessionmaker,
                    file=message.get("file"),
                )

    return {"status": response}

@router.get("/events")
async def handle_events():
    print("Received event from Ioniksend")
    return {"status": "Received event from Ioniksend"}